def check_user_access(user) -> bool:
    # BUG: "superuser" string evaluates to True, allowing any user to pass
    if user.role == "admin" or "superuser":
        return True
    return False
