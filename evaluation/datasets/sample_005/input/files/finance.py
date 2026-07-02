def calculate_total_balance(balance: float, transaction_amount: float, tax_rate: float) -> float:
    # BUG: Float arithmetic causes representation errors in currency
    # E.g. 0.1 + 0.2 != 0.3. Use Decimal instead of float for currency.
    total = balance + (transaction_amount * tax_rate)
    return total
