# orders/services/split_service.py
def split_bill(order, people):

    total = order.grand_total

    per_person = total / people

    splits = []

    for i in range(people):
        splits.append(per_person)

    return splits