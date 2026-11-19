CREATE TABLE IF NOT EXISTS roles (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    permissions TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role_id BIGINT NOT NULL REFERENCES roles(id),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_case_insensitive
    ON users (lower(username));

CREATE TABLE IF NOT EXISTS categories (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS suppliers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    contact_person TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category_id BIGINT REFERENCES categories(id),
    supplier_id BIGINT REFERENCES suppliers(id),
    selling_price NUMERIC(12,2) NOT NULL CHECK (selling_price >= 0),
    acquisition_cost NUMERIC(12,2) NOT NULL CHECK (acquisition_cost >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    reorder_level INTEGER NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS packages (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    selling_price NUMERIC(12,2) NOT NULL CHECK (selling_price >= 0),
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS package_components (
    package_id BIGINT NOT NULL REFERENCES packages(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (package_id, product_id)
);

CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    tin TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    business_style TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id BIGSERIAL PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    supplier_id BIGINT NOT NULL REFERENCES suppliers(id),
    status TEXT NOT NULL CHECK (status IN ('draft', 'ordered', 'partially received', 'received', 'cancelled')),
    expected_date DATE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id BIGSERIAL PRIMARY KEY,
    purchase_order_id BIGINT NOT NULL REFERENCES purchase_orders(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity_ordered INTEGER NOT NULL CHECK (quantity_ordered > 0),
    quantity_received INTEGER NOT NULL DEFAULT 0 CHECK (quantity_received >= 0),
    expected_unit_cost NUMERIC(12,2) NOT NULL CHECK (expected_unit_cost >= 0),
    UNIQUE (purchase_order_id, product_id)
);

CREATE TABLE IF NOT EXISTS receiving_receipts (
    id BIGSERIAL PRIMARY KEY,
    purchase_order_id BIGINT NOT NULL REFERENCES purchase_orders(id),
    reference TEXT NOT NULL UNIQUE,
    received_by BIGINT REFERENCES users(id),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id BIGSERIAL PRIMARY KEY,
    receipt_id BIGINT NOT NULL REFERENCES receiving_receipts(id),
    purchase_order_item_id BIGINT NOT NULL REFERENCES purchase_order_items(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    accepted_unit_cost NUMERIC(12,2) NOT NULL CHECK (accepted_unit_cost >= 0)
);

CREATE TABLE IF NOT EXISTS sales (
    id BIGSERIAL PRIMARY KEY,
    receipt_number TEXT NOT NULL UNIQUE,
    customer_id BIGINT REFERENCES customers(id),
    customer_po_reference TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('open', 'partially dispatched', 'dispatched', 'cancelled', 'refunded')),
    payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'partial', 'paid')),
    discount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount >= 0),
    vat_rate NUMERIC(6,5) NOT NULL,
    vat_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
    total NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sale_items (
    id BIGSERIAL PRIMARY KEY,
    sale_id BIGINT NOT NULL REFERENCES sales(id),
    product_id BIGINT REFERENCES products(id),
    package_id BIGINT REFERENCES packages(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    dispatched_quantity INTEGER NOT NULL DEFAULT 0 CHECK (dispatched_quantity >= 0),
    returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    unit_cost NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    line_total NUMERIC(12,2) NOT NULL CHECK (line_total >= 0),
    CHECK ((product_id IS NOT NULL)::int + (package_id IS NOT NULL)::int = 1)
);

CREATE TABLE IF NOT EXISTS sale_dispatches (
    id BIGSERIAL PRIMARY KEY,
    sale_id BIGINT NOT NULL REFERENCES sales(id),
    dispatched_by BIGINT REFERENCES users(id),
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sale_dispatch_items (
    id BIGSERIAL PRIMARY KEY,
    dispatch_id BIGINT NOT NULL REFERENCES sale_dispatches(id),
    sale_item_id BIGINT NOT NULL REFERENCES sale_items(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK (returned_quantity >= 0),
    unit_cost NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    revenue_amount NUMERIC(12,2) NOT NULL CHECK (revenue_amount >= 0),
    gross_revenue NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (gross_revenue >= 0),
    discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    vat_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (vat_amount >= 0),
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    sale_id BIGINT NOT NULL REFERENCES sales(id),
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    method TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    received_by BIGINT REFERENCES users(id),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sale_returns (
    id BIGSERIAL PRIMARY KEY,
    sale_id BIGINT NOT NULL REFERENCES sales(id),
    reference TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    refund_amount NUMERIC(12,2) NOT NULL CHECK (refund_amount >= 0),
    approved_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS return_items (
    id BIGSERIAL PRIMARY KEY,
    return_id BIGINT NOT NULL REFERENCES sale_returns(id),
    sale_item_id BIGINT NOT NULL REFERENCES sale_items(id),
    dispatch_item_id BIGINT NOT NULL REFERENCES sale_dispatch_items(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    saleable_quantity INTEGER NOT NULL CHECK (saleable_quantity >= 0),
    unit_cost NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    refund_amount NUMERIC(12,2) NOT NULL CHECK (refund_amount >= 0),
    net_revenue_reversed NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (net_revenue_reversed >= 0)
);

CREATE TABLE IF NOT EXISTS stock_adjustment_requests (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity_change INTEGER NOT NULL CHECK (quantity_change <> 0),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('Adjustment In', 'Adjustment Out', 'Loss')),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by BIGINT REFERENCES users(id),
    reviewed_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('Stock In', 'Stock Out', 'Adjustment In', 'Adjustment Out', 'Loss')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    previous_balance INTEGER NOT NULL CHECK (previous_balance >= 0),
    resulting_balance INTEGER NOT NULL CHECK (resulting_balance >= 0),
    unit_cost NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    affects_stock BOOLEAN NOT NULL DEFAULT TRUE,
    reason TEXT NOT NULL,
    reference TEXT NOT NULL,
    user_id BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    action TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backup_logs (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    actor TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_products_active_stock ON products(active, stock_quantity);
CREATE INDEX IF NOT EXISTS idx_movements_product_date ON stock_movements(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_created_date ON sales(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_status_date ON sales(status, dispatched_at DESC);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_dispatch_items_date ON sale_dispatch_items(dispatched_at DESC);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_status ON purchase_orders(status, created_at DESC);

INSERT INTO roles (name, permissions) VALUES
    ('Administrator / Owner', ARRAY['*']),
    ('Sales Staff / Cashier', ARRAY['products.read', 'sales.create', 'sales.read', 'receipts.read']),
    ('Inventory Staff', ARRAY['products.read', 'products.manage', 'inventory.read', 'inventory.adjust.submit', 'suppliers.manage', 'restocking.manage']),
    ('Accounting Staff', ARRAY['sales.read', 'reports.read', 'reports.export', 'receipts.read'])
ON CONFLICT (name) DO NOTHING;
