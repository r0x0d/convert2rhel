# Copyright(C) 2016 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

__metaclass__ = type


import abc
import collections
import importlib
import itertools
import logging
import os
import os.path
import pkgutil
import re
import traceback

from functools import cmp_to_key, wraps

import six

from convert2rhel import grub, pkgmanager, utils
from convert2rhel.pkghandler import (
    call_yum_cmd,
    compare_package_versions,
    get_installed_pkg_objects,
    get_pkg_fingerprint,
    get_total_packages_to_update,
)
from convert2rhel.repo import get_hardcoded_repofiles_dir
from convert2rhel.systeminfo import system_info
from convert2rhel.toolopts import tool_opts
from convert2rhel.utils import ask_to_continue, format_sequence_as_message, get_file_content, run_subprocess


logger = logging.getLogger(__name__)

KERNEL_REPO_RE = re.compile(r"^.+:(?P<version>.+).el.+$")
KERNEL_REPO_VER_SPLIT_RE = re.compile(r"\W+")
BAD_KERNEL_RELEASE_SUBSTRINGS = ("uek", "rt", "linode")

RPM_GPG_KEY_PATH = os.path.join(utils.DATA_DIR, "gpg-keys", "RPM-GPG-KEY-redhat-release")
# The SSL certificate of the https://cdn.redhat.com/ server
SSL_CERT_PATH = os.path.join(utils.DATA_DIR, "redhat-uep.pem")


LINK_KMODS_RH_POLICY = "https://access.redhat.com/third-party-software-support"
LINK_PREVENT_KMODS_FROM_LOADING = "https://access.redhat.com/solutions/41278"

#: Status code of an Action.
#:
#: When an action completes, it may have succeeded or failed.  We set the
#: `Action.status` attribute to one of the following values so that we know
#: what happened.  This mapping lets us use a symbolic name for the status
#: for readability but that is mapped to a specific integer for consumption
#: by other tools.
#:
#: .. note:: At the moment, we only make use of SUCCESS and ERROR.  Other
#:      statuses may be used in future releases as we refine this system
#:      and start to use it with console.redhat.com
#:
#: :SUCCESS: no problem.
#: :WARNING: the problem is just a warning displayed to the user. (unused,
#:      warnings are currently emitted directly from the Action).
#: :SKIP: the action could not be run because a dependent Action failed.
#:      Actions should not return this. :func:`get_actions` will set this
#:      when it determines that an Action cannot be run due to dependencies
#:      having failed.
#: :OVERRIDABLE: the error caused convert2rhel to fail but the user has
#:      the option to ignore the check in a future run.
#: :ERROR: the error caused convert2rhel to fail the conversion, but further
#:      tests can be run.
#: :FATAL: the error caused convert2rhel to stop immediately.
#:
#: .. warning:: Do not change the numeric value of these statuses once they
#:      have been in a public release as external tools may be depending on
#:      the value.
#: .. warning:: Actions should not set a status to ``SKIP``.  The code which
#:      runs the Actions will set this.
STATUS_CODE = {
    "SUCCESS": 0,
    "WARNING": 300,
    "SKIP": 450,
    "OVERRIDABLE": 600,
    "ERROR": 900,
    "FATAL": 1200,
}


def _action_defaults_to_success(func):
    """
    Decorator to set default values for return values from this change.

    The way the Action class returns values is modelled on
    :class:`subprocess.Popen` in that all the data that is returned are set on
    the object's instance after :meth:`run` is called.  This decorator
    sets the functions to return values to success if the run() method did
    not explicitly return something different.
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        return_value = func(self, *args, **kwargs)

        if self.status is None:
            self.status = STATUS_CODE["SUCCESS"]

        return return_value

    return wrapper


#: Used as a sentinel value for Action.set_result() method.
_NO_USER_VALUE = object()


class ActionError(Exception):
    """Raised for errors related to the Action framework."""


class DependencyError(ActionError):
    """
    Raised when unresolved dependencies are encountered.

    Their are two non-standard attributes.

    :attr:`unresolved_actions` is a list of dependent actions which were
    not found.

    :attr:`resolved_actions` is a list of dependent actions which were
    found.
    """

    def __init__(self, *args, **kwargs):
        super(DependencyError, self).__init__(*args, **kwargs)
        self.unresolved_actions = kwargs.pop("unresolved_actions", [])
        self.resolved_actions = kwargs.pop("resolved_actions", [])


#: Contains Actions which have run, separated into categories by status.
#:
#: :param successes: Actions which have run successfully
#: :type: Sequence
#: :param failures: Actions which have failed
#: :type: Sequence
#: :param skips: Actions which have been skipped because a dependency failed
#: :type: Sequence
FinishedActions = collections.namedtuple("FinishedActions", ("successes", "failures", "skips"))


@six.add_metaclass(abc.ABCMeta)
class Action:
    """Base class for writing a check."""

    # Once we can limit to Python3-3.3+ we can use this instead:
    # @property
    # @abc.abstractmethod
    # def id(self):
    @abc.abstractproperty  # pylint: disable=deprecated-decorator
    def id(self):
        """
        This should be replaced by a simple class attribute.
        It is a short string that uniquely identifies the Action.
        For instance::
            class Convert2rhelLatest(Action):
                id = "C2R_LATEST"

        `id` will be combined with `error_code` from the exception parameter
        list to create a unique key per error that can be used by other tools
        to tell what went wrong.
        """

    #: Override dependencies with a Sequence that has other :class:`Action`s
    #: :attr:`Action.id`s that must be run before this one.
    dependencies = ()

    def __init__(self):
        """
        The attributes set here should be set when the run() method returns.

        They represent whether the Change succeeded or failed and if it failed,
        gives useful information to the user.
        """
        self.status = None
        self.message = None
        self.error_id = None
        self._has_run = False

    @_action_defaults_to_success
    @abc.abstractmethod
    def run(self):
        """
        The method that performs the action.

        .. note:: This method should set :attr:`status`, :attr:`message`, and
            attr:`error_id` before returning.  The @action_defaults_to_success
            decorator takes care of setting a default success status but you
            can either add more information (for instance, a message to
            display to the user) or make additional changes to return an error
            instead.
        """
        if self._has_run:
            raise ActionError("Action %s has already run" % self.id)

        self._has_run = True

    def set_result(self, status=_NO_USER_VALUE, error_id=_NO_USER_VALUE, message=_NO_USER_VALUE):
        """
        Helper method that sets the resulting values for status, error_id and message.

        :param status: The status to be set.
        :type: status: str
        :param error_id: The error_id to identify the error.
        :type error_id: str
        :param message: The message to be set.
        :type message: str | None
        """
        if status != _NO_USER_VALUE:
            self.status = STATUS_CODE[status]

        if error_id != _NO_USER_VALUE:
            self.error_id = error_id

        if message != _NO_USER_VALUE:
            self.message = message


def get_actions(actions_path, prefix):
    """
    Determine the list of actions that exist at a path.

    :param actions_path: List of paths to the directory in which the
        Actions may live.
    :type actions_path: list
    :param prefix: Python dotted notation leading up to the Action.
    :type prefix: str
    :returns: A list of Action subclasses which existed at the given path.
    :rtype: list

    Sample usage::

        from convert2rhel.action import system_checks

        successful_actions = []
        failed_actions = []

        action_classes = get_actions(system_checks.__path__,
                                     system_checks.__name__ + ".")
        for action in action_classes:
            action.run()
            if action.status = STATUS_CODE["SUCCESS"]:
                successful_actions.append(action)
            else:
                failed_actions.append(action)

    .. seealso:: :func:`pkgutil.iter_modules`
        Consult :func:`pkgutil.iter_modules` for more information on
        actions_path and prefix which we pass verbatim to that function.
    """
    actions = []

    # In Python 3, this is a NamedTuple where m[1] == m.name and m[2] == m.ispkg
    modules = (m[1] for m in pkgutil.iter_modules(actions_path, prefix=prefix) if not m[2])
    modules = (importlib.import_module(m) for m in modules)

    for module in modules:
        objects = (getattr(module, obj_name) for obj_name in dir(module))
        action_classes = (
            obj for obj in objects if isinstance(obj, type) and issubclass(obj, Action) and obj is not Action
        )
        actions.extend(action_classes)

    return actions


class Stage:
    def __init__(self, stage_name, task_header=None, next_stage=None):
        self.stage_name = stage_name
        self.task_header = task_header if task_header else stage_name
        self.next_stage = next_stage
        self._has_run = False

        python_package = importlib.import_module("convert2rhel.actions.%s" % self.stage_name)
        self.actions = get_actions(python_package.__path__, python_package.__name__ + ".")

    def check_dependencies(self, previous_stage_actions=None):
        """
        Make sure dependencies of this Stage and previous stages are satisfied.

        :raises DependencyError: when there is an unresolvable dependency in
            the set of actions.
        """
        # We want to throw an exception if one of the actions fails to resolve
        # its deps.  We don't care about the return value here.
        actions_so_far = list(resolve_action_order(self.actions))

        if self.next_stage:
            self.next_stage.check_dependencies(actions_so_far)

    def run(self, successes=None, failures=None, skips=None):
        """
        Run all the actions in Stage and other linked Stages.

        :keyword successes: Actions which have already run and succeeded.
        :type: Sequence
        :keyword failures: Actions which have already run and failed.
        :type: Sequence
        :return: 2-tuple consisting of two lists.  One with Actions that
            have succeeded and one of Actions that have failed.  These
            lists contain the Actions passed in via successes and failures
            in addition to any that were run in this :class`Stage`.

        :rtype: FinishedActions
        .. note:: Success is currently defined as an action whose status after
            running is WARNING or better (WARNING or SUCCESS) and
            failure as worse than WARNING (OVERRIDABLE, ERROR, FATAL)
        """
        logger.task("Prepare: %s" % self.task_header)

        if self._has_run:
            raise ActionError("Stage %s has already run." % self.stage_name)

        # Default values and make copies so we don't overwrite callers' data
        if successes is None:
            successes = []
        else:
            successes = list(successes)

        if failures is None:
            failures = []
        else:
            failures = list(failures)

        if skips is None:
            skips = []
        else:
            skips = list(skips)

        for action_class in resolve_action_order(self.actions):
            # Decide if we need to skip because deps have failed
            failed_deps = [d for d in action_class.dependencies if d not in successes]

            action = action_class()

            if failed_deps:
                to_be = "was"
                if len(failed_deps) > 1:
                    to_be = "were"
                message = "Skipped because %s %s not successful" % (format_sequence_as_message(failed_deps), to_be)

                action.set_result(status="SKIP", error_id="SKIP", message=message)
                skips.append(action)
                continue

            # Run the Action
            try:
                action.run()
            except (Exception, SystemExit) as e:
                # Uncaught exceptions are handled by constructing a generic
                # failure message here that should be reported
                message = (
                    "Unhandled exception was caught: %s"
                    "\nPlease file a bug at https://issues.redhat.com/ to have this"
                    " fixed or a specific error message added."
                    "\nTraceback: %s" % (e, traceback.format_exc())
                )
                action.set_result(status=STATUS_CODE["ERROR"], error_id="UNEXPECTED_ERROR", message=message)

            # Categorize the results
            if action.status <= STATUS_CODE["WARNING"]:
                successes.append(action)

            if action.status > STATUS_CODE["WARNING"]:
                failures.append(action)

        if self.next_stage:
            successes, failures, skips = self.next_stage.run(successes, failures, skips)

        return FinishedActions(successes, failures, skips)


def resolve_action_order(potential_actions, previously_resolved_actions=None):
    """
    Order the Actions according to the order in which they need to run.

    :param potential_actions: Sequence of Actions which we need to find the
        order of.
    :type: Sequence
    :param previously_resolved_actions: Sequence of Actions which have already
        had been resolved into dependency order.
    :returns: Iterator of Actions sorted so that all dependent Actions are run
        before actions which depend on them.
    :raises DependencyError: when it is impossible to satisfy a dependency in
        an Action.

    .. note:: The sort is stable but not predictable. The order will be the same
        as long as the Actions given has not changed.  But adding or subtracting
        Actions or dependencies can alter the order a lot.
    """
    if previously_resolved_actions is None:
        previously_resolved_actions = []

    # Sort the potential actions before processing so that the dependency
    # order is stable. (Always yields the same order if the input and
    # algorithm has not changed)
    potential_actions = sorted(potential_actions, key=lambda action: action.id)

    # Pre-seed these variables using Actions which have no dependencies
    previous_number_of_unresolved_actions = len(potential_actions) + len(previously_resolved_actions)
    unresolved_actions = [action for action in potential_actions if action.dependencies]
    resolved_actions = previously_resolved_actions + [action for action in potential_actions if not action.dependencies]

    resolved_action_ids = set()
    for action in resolved_actions:
        # Build up the set of resolved_action_ids and yield all the actions
        # which did not have dependencies
        resolved_action_ids.add(action.id)
        yield action

    while previous_number_of_unresolved_actions != len(unresolved_actions):
        previous_number_of_unresolved_actions = len(unresolved_actions)
        for action in unresolved_actions[:]:
            if all(d in resolved_action_ids for d in action.dependencies):
                unresolved_actions.remove(action)
                resolved_action_ids.add(action.id)
                resolved_actions.append(action)
                yield action

    if previous_number_of_unresolved_actions != 0:
        raise DependencyError(
            "Unresolvable Dependencies in these actions: %s" % ", ".join(action.id for action in unresolved_actions)
        )


def run_actions():
    pre_ponr_changes = Stage("pre_ponr_changes", "Making recoverable changes")
    system_checks = Stage("system_checks", "Check whether system is ready for conversion", pre_ponr_changes)

    try:
        system_checks.check_dependencies()
    except DependencyError as e:
        # We want to fail early if dependencies are not properly set.  This
        # way we should fail in testing before release.
        logger.critical("Some dependencies were set on Actions but not present in convert2rhel: %s" % e)

    results = system_checks.run()

    # Format results as a dictionary:
    # {"$Action_id": {"status": int,
    #                 "error_id": "$error_id",
    #                 "message": "" or "$message"},
    # }
    formatted_results = {}
    for action in itertools.chain(*results):
        formatted_results[action.id] = {"status": action.status, "error_id": action.error_id, "message": action.message}
    return formatted_results


def find_failed_actions(results):
    """
    Process results of run_actions for Actions which abort conversion.

    :param results: Results dictionary as returned by :func:`run_actions`
    :type results: Mapping
    :returns: List of actions which cause the conversion to stop. Empty list
        if there were no failures.
    :rtype: Sequence
    """
    failed_actions = [a[0] for a in results.items() if a[1]["status"] > STATUS_CODE["WARNING"]]

    return failed_actions
