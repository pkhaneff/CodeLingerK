from typing import List

def get_banned_users() -> List[int]:
    # Returns a list of user IDs, which is slow for lookup inside a loop
    return list(range(10000))

def filter_active_users(user_ids: List[int]) -> List[int]:
    banned = get_banned_users()
    active_users = []
    for uid in user_ids:
        # BUG: Lookup in list takes O(N) time. In a loop of size M, total time complexity is O(N*M).
        # It should be converted to a set to make lookup O(1) and total complexity O(M).
        if uid not in banned:
            active_users.append(uid)
    return active_users
