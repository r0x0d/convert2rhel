from envparse import env


def test_check_user_response(convert2rhel):

    # Run c2r registration with no username and password provided
    # check for user prompt enforcing input, then continue with registration
    with convert2rhel(
        "-y --no-rpm-va --serverurl {}".format(
            env.str("RHSM_SERVER_URL"),
        )
    ) as c2r:
        c2r.expect(" ... activation key not found, username and password required")
        c2r.expect("Username: ")
        c2r.sendline()
        c2r.expect("Username: ")
        # Provide username, expect password prompt
        c2r.sendline(env.str("RHSM_USERNAME"))
        c2r.expect("Password: ")
        c2r.sendline()
        c2r.expect("Password: ")
        # Provide password, expect registration
        c2r.sendline(env.str("RHSM_PASSWORD"))
        c2r.expect("Registering the system using subscription-manager ...")
        c2r.expect("Enter number of the chosen subscription: ")
        c2r.sendcontrol("d")
    assert c2r.exitstatus != 0

    with convert2rhel(
        "-y --no-rpm-va --serverurl {} -k {}".format(env.str("RHSM_SERVER_URL"), env.str("RHSM_KEY"))
    ) as c2r:
        c2r.expect("Checking for activation key ...")
        c2r.expect("Organization: ")
        c2r.sendline()
        c2r.expect("Organization: ")
        c2r.sendline(env.str("RHSM_ORG"))
        c2r.expect("Registering the system using subscription-manager ...")
        c2r.sendcontrol("d")
    assert c2r.exitstatus != 0
