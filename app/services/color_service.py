"""
Color generation for contexts and skills.
"""

PALETTE = [
    '#3b82f6',  # blue
    '#8b5cf6',  # violet
    '#06b6d4',  # cyan
    '#10b981',  # emerald
    '#f59e0b',  # amber
    '#ef4444',  # red
    '#ec4899',  # pink
    '#6366f1',  # indigo
    '#84cc16',  # lime
    '#14b8a6',  # teal
]

def generate_color(name: str) -> str:
    """Generate a consistent color for a name using hash."""
    hash_val = 0
    for char in name:
        hash_val = ((hash_val << 5) - hash_val) + ord(char)
        hash_val = hash_val & 0xFFFFFFFF
    
    return PALETTE[hash_val % len(PALETTE)]
