from __future__ import print_function

import fileinput

from conftest import TEST_VARS


target_line = "GRUB_CMDLINE_LINUX"


def test_valid_changes_to_grub_file(convert2rhel):
    """
    Modify the /etc/default/grub file with 'valid' changes adding newlines, whitespaces and comments.
    Valid meaning that none of the changes should cause any issue calling 'grub2-mkconfig'.
    The changes made to the grub file result into:
    5 GRUB_TERMINAL_OUTPUT="foo"
    6
    7 # comment added by test
    8      GRUB_CMDLINE_LINUX="bar"     # comment added by test
    9 # comment added by test
    10
    11 GRUB_DISABLE_RECOVERY="foobar"
    """
    block_comment = "\n# comment added by test\n"
    inline_comment_post = "# comment added by test"
    whitespace = "     "
    for line in fileinput.FileInput("/etc/default/grub", inplace=True):
        if target_line in line:
            line = line.replace("\n", "")
            line = line.replace(
                line,
                block_comment + whitespace + line + whitespace + inline_comment_post + block_comment + "\n",
            )
        print(line, end="")

    with convert2rhel(
        "-y --serverurl {} --username {} --password {} --debug".format(
            TEST_VARS["RHSM_SERVER_URL"],
            TEST_VARS["RHSM_SCA_USERNAME"],
            TEST_VARS["RHSM_SCA_PASSWORD"],
        )
    ) as c2r:
        assert c2r.expect("Successfully updated GRUB2 on the system.") == 0
    assert c2r.exitstatus == 0
