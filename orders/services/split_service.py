from decimal import Decimal

def split_bill(order, people):
    """
    Splits the order total among a number of people.
    Uses Decimal for precision and adds any rounding remainder to the last person.
    """
    if people <= 0:
        return []

    total = order.grand_total
    
    # Calculate base split
    per_person = (total / Decimal(str(people))).quantize(Decimal("0.01"))
    
    splits = [per_person] * (people - 1)
    
    # Calculate remainder for the last person
    sum_others = per_person * (people - 1)
    last_person = total - sum_others
    
    splits.append(last_person)
    
    return splits