INSERT INTO returns (return_id, order_reference, customer_email, item_details, status) VALUES
(
    uuid_generate_v4(),
    'ORD12345',
    'alice@example.com',
    '{"sku": "TS-001", "name": "Blue T-Shirt"}',
    'REQUESTED'
),
(
    uuid_generate_v4(),
    'ORD23456',
    'bob@example.com',
    '{"sku": "LP-002", "name": "Laptop Pro 15"}',
    'APPROVED'
),
(
    uuid_generate_v4(),
    'ORD34567',
    'carol@example.com',
    '{"sku": "HD-010", "name": "Noise Cancelling Headphones"}',
    'REJECTED'
),
(
    uuid_generate_v4(),
    'ORD45678',
    'dave@example.com',
    '{"sku": "MS-004", "name": "Wireless Mouse"}',
    'REQUESTED'
);

INSERT INTO orders (order_id, order_reference, customer_email, status, items) VALUES
(
    uuid_generate_v4(),
    'ORD12345',
    'alice@example.com',
    'SHIPPED',
    '[{"sku": "TS-001", "name": "Blue T-Shirt", "quantity": 2}]'
),
(
    uuid_generate_v4(),
    'ORD23456',
    'bob@example.com',
    'DELIVERED',
    '[{"sku": "LP-002", "name": "Laptop Pro 15", "quantity": 1}]'
),
(
    uuid_generate_v4(),
    'ORD34567',
    'carol@example.com',
    'PROCESSING',
    '[{"sku": "HD-010", "name": "Noise Cancelling Headphones", "quantity": 1}]'
),
(
    uuid_generate_v4(),
    'ORD45678',
    'dave@example.com',
    'DELIVERED',
    '[
        {"sku": "MS-004", "name": "Wireless Mouse", "quantity": 1},
        {"sku": "KB-005", "name": "Mechanical Keyboard", "quantity": 1}
     ]'
);
