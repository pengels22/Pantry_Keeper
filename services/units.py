"""Explicit US customary units; no ingredient-dependent weight/volume conversions."""
import math
from fastapi import HTTPException

UNITS = {
    "each": ("count", 1),
    "oz": ("weight", 28.349523125), "lb": ("weight", 453.59237),
    "g": ("weight", 1), "kg": ("weight", 1000),
    "ml": ("volume", 1), "L": ("volume", 1000),
    "tsp": ("volume", 4.92892159375), "tbsp": ("volume", 14.78676478125),
    "cup": ("volume", 236.5882365), "pint": ("volume", 473.176473),
    "quart": ("volume", 946.352946), "gallon": ("volume", 3785.411784),
}
ALIASES = {
    "each": "each", "ea": "each", "count": "each", "piece": "each", "pieces": "each",
    "oz": "oz", "ounce": "oz", "ounces": "oz", "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "g": "g", "gram": "g", "grams": "g", "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "l": "L", "liter": "L", "liters": "L",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "cup": "cup", "cups": "cup", "pint": "pint", "pints": "pint", "quart": "quart", "quarts": "quart",
    "gallon": "gallon", "gallons": "gallon",
}


def normalize_unit(value):
    if not isinstance(value, str) or value.strip().lower() not in ALIASES:
        raise HTTPException(400, "Unsupported measurement unit.")
    return ALIASES[value.strip().lower()]


def valid_amount(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(400, "Amount must be numeric.")
    amount = float(value)
    if not math.isfinite(amount) or amount < 0 or (positive and amount == 0):
        raise HTTPException(400, "Amount must be finite and nonnegative (positive for proposals).")
    return amount


def convert_amount(amount, source, target):
    amount = valid_amount(amount)
    source, target = normalize_unit(source), normalize_unit(target)
    if UNITS[source][0] != UNITS[target][0]:
        raise HTTPException(400, "Incompatible units; weight and volume cannot be converted automatically.")
    result = amount * UNITS[source][1] / UNITS[target][1]
    return valid_amount(result)
