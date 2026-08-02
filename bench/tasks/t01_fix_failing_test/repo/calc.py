def total_price(prices, discount):
    """
    Calculate the total price after applying a discount.
    prices: list of item prices.
    discount: discount rate (e.g., 0.9 for 10% off).
    Returns the total price after discount.
    """
    total = sum(prices)
    return total + (1 - discount)
