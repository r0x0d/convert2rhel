# -*- coding: utf-8 -*-
#
# Copyright(C) 2018 Red Hat, Inc.
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

import os.path
import re

from collections import defaultdict

import pytest
import six


six.add_move(six.MovedModule("mock", "mock", "unittest.mock"))
from six.moves import mock

from convert2rhel import actions
from convert2rhel.actions import STATUS_CODE


class _ActionForTesting(actions.Action):
    """Fake Action class where we can set all of the attributes as we like."""

    id = None

    def __init__(self, **kwargs):
        super(_ActionForTesting, self).__init__()
        for attr_name, attr_value in kwargs.items():
            setattr(self, attr_name, attr_value)

    # We have to override run() because run() is an abstract method
    # but we don't do anything with it in the unittests
    def run(self):  # pylint: disable=useless-parent-delegation
        super(_ActionForTesting, self).run()


class TestGetActions:
    ACTION_CLASS_DEFINITION_RE = re.compile(r"^class .+\([^)]*Action\):$", re.MULTILINE)

    def test_get_actions_smoketest(self):
        """Test that there are no errors loading the Actions we ship."""
        computed_actions = []

        # Is this method of finding how many Action plugins we ship too hacky?
        filesystem_detected_actions_count = 0
        for rootdir, dirnames, filenames in os.walk(os.path.dirname(actions.__file__)):
            for directory in dirnames:
                # Add to the actions that the production code finds here as it is non-recursive
                computed_actions.extend(
                    actions.get_actions([os.path.join(rootdir, directory)], "%s.%s." % (actions.__name__, directory))
                )

            for filename in (os.path.join(rootdir, filename) for filename in filenames):
                if filename.endswith(".py") and not filename.endswith("/__init__.py"):
                    with open(filename) as f:
                        action_classes = self.ACTION_CLASS_DEFINITION_RE.findall(f.read())
                        filesystem_detected_actions_count += len(action_classes)

        assert len(computed_actions) == filesystem_detected_actions_count

    def test_get_actions_no_dupes(self):
        """Test that there are no duplicates in the list of returned Actions."""
        computed_actions = actions.get_actions(actions.__path__, actions.__name__ + ".")

        assert len(computed_actions) == len(frozenset(computed_actions))

    def test_no_actions(self, tmpdir):
        """No Actions returns an empty list."""
        # We need to make sure this returns an empty list, not just false-y
        assert (
            actions.get_actions([str(tmpdir)], "tmp.") == []  # pylint: disable=use-implicit-booleaness-not-comparison
        )

    @pytest.mark.parametrize(
        (
            "test_dir_name",
            "expected_action_names",
        ),
        (
            ("aliased_action_name", ["RealTest"]),
            ("extraneous_files", ["RealTest"]),
            ("ignore__init__", ["RealTest"]),
            ("multiple_actions_one_file", ["RealTest", "SecondTest"]),
            ("not_action_itself", ["RealTest", "OtherTest"]),
            ("only_subclasses_of_action", ["RealTest"]),
        ),
    )
    def test_found_actions(self, sys_path, test_dir_name, expected_action_names):
        """Set of Actions that we have generated is found."""
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        sys_path.insert(0, data_dir)
        test_data = os.path.join(data_dir, test_dir_name)
        computed_action_names = sorted(
            m.__name__
            for m in actions.get_actions([test_data], "convert2rhel.unit_tests.actions.data.%s." % test_dir_name)
        )
        assert computed_action_names == sorted(expected_action_names)


class TestStage:
    def test_init(self):
        pass

    def test_check_dependencies(self):
        # Run check_dependencies on the real Actions at least once so that we know what we ship
        # works
        pass

    def test_run(self):
        pass


class TestResolveActionOrder:
    @pytest.mark.parametrize(
        ("potential_actions", "ordered_result"),
        (
            ([], []),
            ([_ActionForTesting(id="One")], ["One"]),
            ([_ActionForTesting(id="One"), _ActionForTesting(id="Two", dependencies=("One",))], ["One", "Two"]),
            ([_ActionForTesting(id="Two"), _ActionForTesting(id="One", dependencies=("Two",))], ["Two", "One"]),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Three", dependencies=("Two",)),
                    _ActionForTesting(id="Four", dependencies=("Three",)),
                ],
                ["One", "Two", "Three", "Four"],
            ),
            # Multiple dependencies (Slight differences in order to show
            # that order of deps and Actions doesn't matter.  The sort is
            # still stable.
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(
                        id="Three",
                        dependencies=(
                            "Two",
                            "One",
                        ),
                    ),
                    _ActionForTesting(id="Four", dependencies=("Three",)),
                ],
                ["One", "Two", "Three", "Four"],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Three", dependencies=("Two",)),
                    _ActionForTesting(
                        id="Four",
                        dependencies=(
                            "One",
                            "Three",
                        ),
                    ),
                ],
                ["One", "Two", "Three", "Four"],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Three", dependencies=("Two",)),
                    _ActionForTesting(
                        id="Four",
                        dependencies=(
                            "Three",
                            "One",
                        ),
                    ),
                ],
                ["One", "Two", "Three", "Four"],
            ),
        ),
    )
    def test_one_solution(self, potential_actions, ordered_result):
        """Resolve order when only one solutions satisfies dependencies."""
        computed_actions = actions.resolve_action_order(potential_actions)
        computed_action_ids = [action.id for action in computed_actions]
        assert computed_action_ids == ordered_result

    # Note: Each of these sets of Actions have multiple solutions but
    # the stable sort assurance should guarantee that the order is only a
    # single one of these.  The alternates are commented out to show they
    # aren't really wrong, but we expect that the stable sort will mean we
    # always get the order that is uncommented.  If the algorithm changes
    # between releases, we may want to allow any of the alternates as well.
    @pytest.mark.parametrize(
        ("potential_actions", "possible_orders"),
        (
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Three", dependencies=("One",)),
                    _ActionForTesting(id="Four", dependencies=("Three",)),
                ],
                (
                    # ["One", "Two", "Three", "Four"],
                    ["One", "Three", "Two", "Four"],
                ),
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Three", dependencies=("One",)),
                    _ActionForTesting(id="Four", dependencies=("One",)),
                ],
                (
                    # ["One", "Two", "Three", "Four"],
                    # ["One", "Two", "Four", "Three"],
                    # ["One", "Three", "Two", "Four"],
                    # ["One", "Three", "Four", "Two"],
                    # ["One", "Four", "Two", "Three"],
                    ["One", "Four", "Three", "Two"],
                ),
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two"),
                    _ActionForTesting(id="Three", dependencies=("One",)),
                    _ActionForTesting(id="Four", dependencies=("Two",)),
                ],
                (
                    # ["One", "Two", "Three", "Four"],
                    ["One", "Two", "Four", "Three"],
                    # ["One", "Three", "Two", "Four"],
                    # ["Two", "One", "Three", "Four"],
                    # ["Two", "One", "Four", "Three"],
                    # ["Two", "Four", "One", "Three"],
                ),
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(
                        id="Two",
                        dependencies=(
                            "One",
                            "Three",
                        ),
                    ),
                    _ActionForTesting(id="Three", dependencies=("One",)),
                    _ActionForTesting(id="Four", dependencies=("Three",)),
                ],
                (
                    ["One", "Three", "Two", "Four"],
                    # ["One", "Three", "Four", "Two"],
                ),
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two"),
                    _ActionForTesting(id="Three"),
                ],
                (
                    # ["One", "Two", "Three"],
                    ["One", "Three", "Two"],
                    # ["Two", "One", "Three"],
                    # ["Two", "Three", "One"],
                    # ["Three", "One", "Two"],
                    # ["Three", "Two", "One"],
                ),
            ),
        ),
    )
    def test_multiple_solutions(self, potential_actions, possible_orders):
        """
        When multiple solutions exist, the code chooses a single correct solution.

        This test both checks that the order is correct and that the sort is
        stable (it doesn't change between runs or on different distributionss).
        """
        computed_actions = actions.resolve_action_order(potential_actions)
        computed_action_ids = [action.id for action in computed_actions]
        assert computed_action_ids in possible_orders

    @pytest.mark.parametrize(
        ("potential_actions",),
        (
            # Dependencies that don't exist
            (
                [
                    _ActionForTesting(id="One", dependencies=("Unknown",)),
                ],
            ),
            (
                [
                    _ActionForTesting(id="One", dependencies=("Unknown",)),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                ],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                    _ActionForTesting(id="Two", dependencies=("Unknown",)),
                ],
            ),
            # Circular deps
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("Three",)),
                    _ActionForTesting(id="Three", dependencies=("Two",)),
                ],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("Three",)),
                    _ActionForTesting(id="Three", dependencies=("Four",)),
                    _ActionForTesting(id="Four", dependencies=("Two",)),
                ],
            ),
            (
                [
                    _ActionForTesting(id="One", dependencies=("Three",)),
                    _ActionForTesting(id="Two", dependencies=("Three",)),
                    _ActionForTesting(id="Three", dependencies=("Four",)),
                    _ActionForTesting(id="Four", dependencies=("One",)),
                ],
            ),
        ),
    )
    def test_no_solutions(self, potential_actions):
        """All of these have unsatisfied dependencies."""
        with pytest.raises(actions.DependencyError):
            list(actions.resolve_action_order(potential_actions))

    @pytest.mark.parametrize(
        ("potential", "previous", "ordered_result"),
        (
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(
                        id="Two",
                        dependencies=(
                            "One",
                            "Three",
                        ),
                    ),
                    _ActionForTesting(id="Three", dependencies=("One",)),
                    _ActionForTesting(id="Four", dependencies=("Two",)),
                ],
                [
                    _ActionForTesting(id="Zero"),
                ],
                ["Zero", "One", "Three", "Two", "Four"],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("Zero",)),
                    _ActionForTesting(id="Three", dependencies=("One", "Two")),
                    _ActionForTesting(id="Four", dependencies=("Three",)),
                ],
                [
                    _ActionForTesting(id="Zero"),
                ],
                ["Zero", "One", "Two", "Three", "Four"],
                # ["Zero", "Two", "One", "Three", "Four"],
            ),
            (
                [
                    _ActionForTesting(id="One"),
                    _ActionForTesting(id="Two", dependencies=("One",)),
                ],
                [
                    _ActionForTesting(id="Zero"),
                    _ActionForTesting(id="Three"),
                    _ActionForTesting(id="Zed"),
                ],
                ["Zero", "Three", "Zed", "One", "Two"],
                # ["Zero", "Two", "One", "Three", "Four"],
            ),
            (
                [
                    _ActionForTesting(id="One", dependencies=("Zero",)),
                    _ActionForTesting(
                        id="Two",
                        dependencies=(
                            "Zero",
                            "One",
                        ),
                    ),
                ],
                [
                    _ActionForTesting(id="Zero"),
                ],
                ["Zero", "One", "Two"],
                # ["Zero", "Two", "One", "Three", "Four"],
            ),
        ),
    )
    def test_with_previously_resolved_actions(self, potential, previous, ordered_result):
        computed_actions = actions.resolve_action_order(potential, previously_resolved_actions=previous)

        computed_action_ids = [action.id for action in computed_actions]
        assert computed_action_ids == ordered_result

    @pytest.mark.parametrize(
        ("potential", "previous"),
        (
            (
                [_ActionForTesting(id="One", dependencies=("Unknown",))],
                [
                    _ActionForTesting(id="Zero"),
                ],
            ),
            (
                [_ActionForTesting(id="One"), _ActionForTesting(id="Four", dependencies=("Unknown",))],
                [
                    _ActionForTesting(id="Zero"),
                ],
            ),
        ),
    )
    def test_with_previously_resolved_actions_no_solutions(self, potential, previous):
        with pytest.raises(actions.DependencyError):
            list(actions.resolve_action_order(potential, previous))


class TestRunActions:
    @pytest.mark.parametrize(
        ("action_results", "expected"),
        (
            # Only successes
            (
                actions.FinishedActions([], [], []),
                {},
            ),
            (
                actions.FinishedActions(
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["SUCCESS"]),
                    ],
                    [],
                    [],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["SUCCESS"]},
                },
            ),
            (
                actions.FinishedActions(
                    [
                        _ActionForTesting(
                            id="One", status=STATUS_CODE["WARNING"], error_id="DANGER", message="Warned about danger"
                        ),
                    ],
                    [],
                    [],
                ),
                {
                    "One": {"error_id": "DANGER", "message": "Warned about danger", "status": STATUS_CODE["WARNING"]},
                },
            ),
            (
                actions.FinishedActions(
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["WARNING"]),
                        _ActionForTesting(id="Two", status=STATUS_CODE["SUCCESS"], dependencies=("One",)),
                        _ActionForTesting(
                            id="Three",
                            status=STATUS_CODE["SUCCESS"],
                            dependencies=(
                                "One",
                                "Two",
                            ),
                        ),
                    ],
                    [],
                    [],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["WARNING"]},
                    "Two": {"error_id": None, "message": None, "status": STATUS_CODE["SUCCESS"]},
                    "Three": {"error_id": None, "message": None, "status": STATUS_CODE["SUCCESS"]},
                },
            ),
            # Single Failures
            (
                actions.FinishedActions(
                    [],
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["ERROR"]),
                    ],
                    [],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["ERROR"]},
                },
            ),
            (
                actions.FinishedActions(
                    [],
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["FATAL"]),
                    ],
                    [],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["FATAL"]},
                },
            ),
            (
                actions.FinishedActions(
                    [],
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["OVERRIDABLE"]),
                    ],
                    [],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["OVERRIDABLE"]},
                },
            ),
            (
                actions.FinishedActions(
                    [],
                    [],
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["SKIP"]),
                    ],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["SKIP"]},
                },
            ),
            # Some failures, some successes
            (
                actions.FinishedActions(
                    [
                        _ActionForTesting(id="One", status=STATUS_CODE["WARNING"]),
                        _ActionForTesting(id="Four", status=STATUS_CODE["SUCCESS"]),
                    ],
                    [
                        _ActionForTesting(id="Two", status=STATUS_CODE["ERROR"]),
                    ],
                    [
                        _ActionForTesting(id="Three", status=STATUS_CODE["SKIP"]),
                    ],
                ),
                {
                    "One": {"error_id": None, "message": None, "status": STATUS_CODE["WARNING"]},
                    "Two": {"error_id": None, "message": None, "status": STATUS_CODE["ERROR"]},
                    "Three": {"error_id": None, "message": None, "status": STATUS_CODE["SKIP"]},
                    "Four": {"error_id": None, "message": None, "status": STATUS_CODE["SUCCESS"]},
                },
            ),
        ),
    )
    def test_run_actions(self, action_results, expected, monkeypatch):
        check_deps_mock = mock.Mock()
        run_mock = mock.Mock(return_value=action_results)

        monkeypatch.setattr(actions.Stage, "check_dependencies", check_deps_mock)
        monkeypatch.setattr(actions.Stage, "run", run_mock)

        assert actions.run_actions() == expected

    def test_dependency_errors(self, monkeypatch, caplog):
        check_deps_mock = mock.Mock(side_effect=actions.DependencyError("Failure message"))
        monkeypatch.setattr(actions.Stage, "check_dependencies", check_deps_mock)

        with pytest.raises(SystemExit):
            actions.run_actions()

        assert (
            "Some dependencies were set on Actions but not present in convert2rhel: Failure message"
            == caplog.records[-1].message
        )


class TestFindFailedActions:
    @pytest.mark.parametrize(
        ("results", "failed"),
        (
            (
                {},
                [],
            ),
            (
                {
                    "TEST": {"status": STATUS_CODE["SUCCESS"], "error_id": "", "message": ""},
                },
                [],
            ),
            (
                {
                    "GOOD": {"status": STATUS_CODE["SUCCESS"], "error_id": "", "message": ""},
                    "GOOD2": {"status": STATUS_CODE["SUCCESS"], "error_id": "TWO", "message": "Nothing to see"},
                },
                [],
            ),
            (
                {
                    "GOOD": {"status": STATUS_CODE["WARNING"], "error_id": "AWARN", "message": "Danger"},
                },
                [],
            ),
            (
                {
                    "BAD": {"status": STATUS_CODE["ERROR"], "error_id": "ERROR", "message": "Explosion"},
                },
                ["BAD"],
            ),
            (
                {
                    "BAD": {"status": STATUS_CODE["ERROR"], "error_id": "ERROR", "message": "Explosion"},
                    "BAD2": {"status": STATUS_CODE["FATAL"], "error_id": "FATAL", "message": "Explosion"},
                    "BAD3": {"status": STATUS_CODE["OVERRIDABLE"], "error_id": "OVERRIDABLE", "message": "Explosion"},
                    "BAD4": {"status": STATUS_CODE["SKIP"], "error_id": "SKIP", "message": "Explosion"},
                    "GOOD": {"status": STATUS_CODE["WARNING"], "error_id": "WARN", "message": "Danger"},
                    "GOOD2": {"status": STATUS_CODE["SUCCESS"], "error_id": "", "message": "No Error here"},
                },
                ["BAD", "BAD2", "BAD3", "BAD4"],
            ),
        ),
    )
    def test_find_failed_actions(self, results, failed):
        assert sorted(actions.find_failed_actions(results)) == sorted(failed)


class TestActions:
    """Tests across all of the Actions we ship."""

    def test_no_duplicate_ids(self):
        """Test that each Action has its own unique id."""
        computed_actions = actions.get_actions(actions.__path__, actions.__name__ + ".")

        action_id_locations = defaultdict(list)
        for action in computed_actions:
            action_id_locations[action.id].append(str(action))

        dupe_actions = []
        for action_id, locations in action_id_locations.items():
            if len(locations) > 1:
                dupe_actions.append("%s is present in more than one location: %s" % (action_id, ", ".join(locations)))

        assert not dupe_actions, "\n".join(dupe_actions)
