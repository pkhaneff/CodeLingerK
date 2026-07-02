import sqlite3

def get_user_data(user_input_id: str):
    # BUG 1 (security): SQL Injection through raw string formatting
    query = f"SELECT * FROM users WHERE id = '{user_input_id}'"
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute(query)
    
    # BUG 2 (logic): Unhandled ValueError when casting string values to float
    # If the database returns None or a non-numeric value, it will crash
    res = cursor.fetchone()
    balance = float(res[2]) if res else 0.0
    
    return balance
