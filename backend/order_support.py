"""Collect order details from the customer; only services decide eligibility."""
import re

from backend import actions

NOT_FOUND = "I couldn't find a FUN SHOES order with that ID for your account. Please check the order ID and try again."
FAILURES = {
    "PROPOSAL_NOT_AVAILABLE": NOT_FOUND,
    "NOT_CANCELLABLE": "Only processing FUN SHOES orders can be cancelled. This order cannot be cancelled.",
    "NOT_RETURNABLE_YET": "Only delivered FUN SHOES items can be returned or exchanged.",
    "RETURN_WINDOW_EXPIRED": "This order is outside the 30-day return/exchange window.",
    "REQUEST_ALREADY_ACTIVE": "An active return or exchange already exists for this item.",
    "OUT_OF_STOCK": "That replacement is out of stock. Please choose another available size.",
    "CONDITION_NOT_ACCEPTED": "This condition is not eligible under the FUN SHOES demo policy.",
    "REPLACEMENT_NOT_ALLOWED": "Choose the same product at the same price for an exchange.",
}


def invalidate(session, proposals):
    if session.pending_proposal:
        proposals.consume(session.pending_proposal)
        session.pending_proposal = None


def handle(session, message, connection, proposals, operations, clock):
    """Return (customer text, proposal) when this is an order-support turn."""
    text = message.strip().lower()
    if re.search(r'\b(amazon|ebay|zalando|walmart)\b', text):
        invalidate(session, proposals)
        session.order_context = None
        return ('I support FUN SHOES only. I cannot access another retailer’s orders or apply its policies.', None)
    policy = bool(re.search(r'\b(policy|policies|how long|how do returns|return window|can i cancel a|can i exchange size|what happens if)\b', text))
    intent_match = re.search(r'\b(return|exchange|cancel|cancellation)\b', text)
    intent = intent_match.group() if intent_match and not policy else None
    if intent == 'cancel':
        intent = 'cancellation'
    ids = re.findall(r'\border_[a-z0-9_-]+\b', text)
    context = session.order_context
    if policy:
        return None
    if not intent and not context:
        if not policy and not re.search(r'\b(buy|purchase)\b', text):
            products = connection.execute('SELECT id, name FROM products').fetchall()
            matches = [p for p in products if p['name'].lower() in text]
            if len(matches) == 1:
                product = matches[0]
                rows = connection.execute(
                    'SELECT v.size, v.colour, v.price_cents, i.on_hand-i.reserved AS available '
                    'FROM variants v JOIN inventory i ON i.variant_id=v.id WHERE v.product_id=? ORDER BY v.size',
                    (product['id'],)).fetchall()
                details = '; '.join(f"EU {v['size']} {v['colour']}: €{v['price_cents']/100:.2f}, {v['available']} available" for v in rows)
                return (f"FUN SHOES {product['name']}: {details}. Which size interests you?", None)
        if ids:
            return ('Would you like to return, exchange, or cancel this FUN SHOES order?', None)
        return None
    if intent and (not context or context['intent'] != intent):
        invalidate(session, proposals)
        context = {'intent': intent}
        session.order_context = context
    if session.customer_id is None:
        return ('Please select a FUN SHOES demo customer, then tell me your request and order ID.', None)
    if re.search(r'\border\s+[a-z0-9]', text) and not ids and context.get('order_id'):
        invalidate(session, proposals)
        context = {'intent': context['intent']}
        session.order_context = context
    if len(set(ids)) > 1:
        invalidate(session, proposals)
        context.pop('order_id', None)
        return ('Please give one FUN SHOES order ID at a time.', None)
    if ids and ids[0] != context.get('order_id'):
        invalidate(session, proposals)
        context = {'intent': context['intent'], 'order_id': ids[0]}
        session.order_context = context
    if not context.get('order_id'):
        return ('What is your FUN SHOES order ID? Please type it exactly (including underscores) if voice recognition is unclear.', None)
    order = connection.execute('SELECT * FROM orders WHERE id = ? AND customer_id = ?',
                               (context['order_id'], session.customer_id)).fetchone()
    if order is None:
        context.pop('order_id', None)
        return (NOT_FOUND, None)
    if context['intent'] == 'cancellation':
        args = {'order_id': order['id']}
    else:
        if order['state'] != 'delivered':
            return (FAILURES['NOT_RETURNABLE_YET'], None)
        lines = connection.execute('SELECT * FROM order_lines WHERE order_id = ?', (order['id'],)).fetchall()
        selected = [line for line in lines if line['item_name'].lower() in text or line['variant_id'] in text]
        item_size = re.search(r'\b(?:item|original|size)\s+(\d{2})\b', text)
        if item_size and context.get('awaiting_item') and not context.get('variant_id'):
            selected = [line for line in (selected or lines) if line['size'] == int(item_size.group(1))]
        if len(selected) > 1:
            selected = [line for line in selected if line['colour'].lower() in text]
        if len(lines) == 1:
            context['variant_id'] = lines[0]['variant_id']
        elif len(selected) == 1:
            context['variant_id'] = selected[0]['variant_id']
        if not context.get('variant_id'):
            context['awaiting_item'] = True
            return ('Which item? ' + ', '.join(f"{line['item_name']} size {line['size']} {line['colour']}" for line in lines), None)
        if re.search(r'\b(unworn|not worn|never worn)\b', text):
            context['condition'] = 'unworn'
        elif re.search(r'\b(worn once|worn_once)\b', text):
            context['condition'] = 'worn_once'
        elif re.search(r'\b(worn|used)\b', text):
            return (FAILURES['CONDITION_NOT_ACCEPTED'], None)
        if re.search(r"does.not.fit|don.t fit|too (small|big|large|tight)|wrong size|does_not_fit", text):
            context['reason'] = 'does_not_fit'
        elif 'not as described' in text or 'not_as_described' in text:
            context['reason'] = 'not_as_described'
        elif re.search(r'\bother\b', text):
            context['reason'] = 'other'
        if context['intent'] == 'exchange':
            size = re.search(r'\b(?:size|for|to)\s+(\d{2})\b', text)
            if size or re.fullmatch(r'\d{2}', text):
                context['size'] = int(size.group(1) if size else text)
        if not context.get('condition'):
            return (f"I found FUN SHOES order {order['id']}. Are the shoes unworn or worn once?", None)
        args = {'order_id': order['id'], 'variant_id': context['variant_id'], 'condition': context['condition']}
        if context['intent'] == 'return':
            if not context.get('reason'):
                return ('What is the return reason: does not fit, not as described, or other?', None)
            args['reason'] = context['reason']
        else:
            if not context.get('size'):
                return ('Which replacement size would you like for the same FUN SHOES product?', None)
            variants = connection.execute(
                'SELECT * FROM variants WHERE product_id = (SELECT product_id FROM variants WHERE id = ?) AND size = ?',
                (context['variant_id'], context['size'])).fetchall()
            if len(variants) != 1:
                context.pop('size', None)
                return ('That size is unavailable or ambiguous. Please choose another size.', None)
            args['replacement_variant_id'] = variants[0]['id']
    invalidate(session, proposals)
    try:
        if context['intent'] == 'cancellation':
            proposal = actions.create_cancellation_proposal(connection, proposals, session, args, operations)
        elif context['intent'] == 'return':
            proposal = actions.create_return_proposal(connection, proposals, session, args, clock=clock)
        else:
            proposal = actions.create_exchange_proposal(connection, proposals, session, args, clock=clock)
    except actions.ProposalError as failure:
        return (FAILURES.get(failure.code, 'This request is not eligible under the FUN SHOES policy. Please check the item and replacement details.'), None)
    if proposal.get('status') == 'already_cancelled':
        session.order_context = None
        return ('This FUN SHOES order is already cancelled.', None)
    if context['intent'] != 'cancellation':
        line = next(line for line in lines if line['variant_id'] == context['variant_id'])
        proposal['terms'] = (f"Order {order['id']}: {line['quantity']} × {line['item_name']}, "
                             f"EU {line['size']}, {line['colour']}. Condition: {context['condition']}. ")
        if context['intent'] == 'exchange':
            proposal['terms'] += f"Replacement: EU {variants[0]['size']}, {variants[0]['colour']}, same price. "
        else:
            proposal['terms'] += f"Reason: {context['reason'].replace('_', ' ')}. "
        proposal['terms'] += 'Request only; no refund, inspection, or shipment occurs in this demo.'
    session.pending_proposal = proposal['proposal_id']
    return (f"Please review and confirm the FUN SHOES {context['intent']} request for {order['id']}. Nothing has been changed yet.", proposal)
