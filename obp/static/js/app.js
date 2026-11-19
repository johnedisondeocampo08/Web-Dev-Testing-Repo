const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let csrfToken = "";
let currentUser = null;

function escapeHtml(value = "") {
  return String(value).replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char],
  );
}

function money(value) {
  const amount = Number(value || 0);
  return `₱${amount.toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function localDate(date = new Date()) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function toast(message, error = false) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("visible"), 3800);
}

async function api(path, options = {}) {
  const headers = { "X-CSRF-Token": csrfToken, ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message =
      typeof body === "object" && body
        ? body.error
        : `Request failed (${response.status}).`;
    throw new Error(message || `Request failed (${response.status}).`);
  }
  return body;
}

function serializeForm(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function statusBadge(value) {
  const styles = {
    "Low stock": "status-warning",
    "Out of stock": "status-danger",
    Paid: "status-success",
    Received: "status-success",
    "Partially received": "status-warning",
    "Partially dispatched": "status-warning",
    Dispatched: "status-success",
    Open: "status-info",
    Cancelled: "status-neutral",
    Refunded: "status-danger",
  };
  const label = String(value || "").replace(/\b\w/g, (letter) =>
    letter.toUpperCase(),
  );
  return `<span class="status ${styles[label] || "status-neutral"}">${escapeHtml(label)}</span>`;
}

function showDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && typeof dialog.showModal === "function") dialog.showModal();
}

document.addEventListener("click", (event) => {
  const openButton = event.target.closest("[data-open-dialog]");
  if (openButton) showDialog(openButton.dataset.openDialog);
  if (event.target.closest("[data-close-dialog]")) {
    event.target.closest("dialog")?.close();
  }
});

function hasPermission(permission) {
  const permissions = currentUser?.permissions || [];
  return permissions.includes("*") || permissions.includes(permission);
}

function showPageLayout(pageContent, pageName) {
  const links = [
    ["dashboard", "Overview", "/", ""],
    ["sales", "Sales processing", "/sales", "sales.create"],
    ["inventory", "Inventory", "/inventory", "products.read"],
    ["suppliers", "Suppliers & restocking", "/suppliers", "suppliers.manage"],
    ["reports", "Sales reports", "/reports", "reports.read"],
    ["receipts", "View receipts", "/receipts", "receipts.read"],
    ["admin", "Users & settings", "/admin", "users.manage"],
  ];
  document.body.innerHTML = `<div class="app-shell">
    <aside class="sidebar"><a class="brand" href="/"><span class="brand-mark">OB</span><span><strong>optimum</strong><small>BUSINESS PRODUCTS</small></span></a>
      <div class="workspace-label">WORKSPACE</div><nav id="main-navigation" class="side-nav" aria-label="Main navigation"></nav>
      <div class="sidebar-bottom"><span class="online-dot"></span><span>Inventory system active</span></div></aside>
    <div class="main-area"><header class="topbar"><div class="breadcrumb">OBP <span>/</span> <span id="current-page-name"></span></div>
      <div class="user-area"><div id="user-avatar" class="user-avatar"></div><div class="user-info"><strong id="user-name"></strong><small id="user-role"></small></div>
      <button id="sign-out" class="button button-quiet button-small" type="button">Sign out</button></div></header>
      <div class="page-content"><div id="toast" class="toast" role="status" aria-live="polite"></div></div></div></div>`;
  const names = new Map(links.map(([key, name]) => [key, name]));
  const navigation = $("#main-navigation");
  for (const [key, name, url, permission] of links) {
    if (permission && !hasPermission(permission)) continue;
    const link = document.createElement("a");
    link.href = url;
    link.className = key === pageName ? "active" : "";
    link.textContent = name;
    navigation.append(link);
  }
  $("#current-page-name").textContent = names.get(pageName) || "Workspace";
  $("#user-name").textContent = currentUser.display_name;
  $("#user-role").textContent = currentUser.role_name;
  $("#user-avatar").textContent = currentUser.display_name
    .charAt(0)
    .toUpperCase();
  $(".page-content").append(pageContent);

  $$("[data-permission]").forEach((element) => {
    const permissions = element.dataset.permission.split(" ");
    element.hidden = !permissions.some(hasPermission);
  });
  const inventoryPanel = $("#inventory-panel");
  if (inventoryPanel)
    inventoryPanel.dataset.canManage = String(hasPermission("products.manage"));
  const receiptDialog = $("#receipt-dialog");
  if (receiptDialog)
    receiptDialog.dataset.canRefund = String(hasPermission("refunds.approve"));

  $("#sign-out").addEventListener("click", async () => {
    try {
      await api("/logout", { method: "POST", body: "{}" });
      location.href = "/login";
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function initLogin() {
  if (currentUser) {
    location.href = "/";
    return;
  }
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = $("#login-error");
    message.hidden = true;
    try {
      const result = await api("/login", {
        method: "POST",
        body: JSON.stringify(serializeForm(event.currentTarget)),
      });
      csrfToken = result.csrf_token;
      location.href = result.redirect;
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  });
}

async function initDashboard() {
  if (!$("#today-label")) return;
  try {
    const data = await api("/api/dashboard");
    $("#today-label").textContent = data.today_label.toUpperCase();
    $("#welcome-message").textContent =
      `Good to see you, ${currentUser.display_name.split(" ")[0]}.`;
    $("#today-sales").textContent = money(data.metrics.today_sales);
    $("#product-count").textContent = data.metrics.products;
    $("#low-stock-count").textContent = data.metrics.low_stock;
    $("#open-order-count").textContent = data.metrics.open_orders;
    const alerts = $("#inventory-alerts");
    alerts.innerHTML = data.alerts.length
      ? `<table><thead><tr><th>Product</th><th>Supplier</th><th>On hand</th><th>Status</th></tr></thead><tbody>${data.alerts
          .map(
            (item) => `
      <tr><td><strong>${escapeHtml(item.name)}</strong><small class="table-sub">${escapeHtml(item.sku)}</small></td>
      <td>${escapeHtml(item.supplier)}</td><td class="mono">${item.stock_quantity} / ${item.reorder_level}</td>
      <td>${statusBadge(item.stock_quantity === 0 ? "Out of stock" : "Low stock")}</td></tr>`,
          )
          .join("")}</tbody></table>`
      : '<div class="empty-inline"><strong>All clear</strong><small>No products at or below their reorder level.</small></div>';
    const sales = $("#recent-sales");
    sales.innerHTML = data.recent_sales.length
      ? `<table><thead><tr><th>Receipt</th><th>Status</th><th>Total</th></tr></thead><tbody>${data.recent_sales
          .map(
            (sale) => `
      <tr><td class="mono">${escapeHtml(sale.receipt_number)}</td><td>${statusBadge(sale.status)}</td><td class="mono">${money(sale.total)}</td></tr>`,
          )
          .join("")}</tbody></table>`
      : '<div class="empty-inline"><strong>No sales yet</strong><small>Completed transactions will appear here.</small></div>';
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadSales() {
  const table = $("#sales-table");
  if (!table) return;
  try {
    const { sales } = await api("/api/sales");
    table.innerHTML = sales.length
      ? sales
          .map(
            (sale) => `
      <tr>
        <td><strong class="mono">${escapeHtml(sale.receipt_number)}</strong></td>
        <td>${new Date(sale.created_at).toLocaleString()}</td>
        <td>${escapeHtml(sale.customer)}</td>
        <td>${statusBadge(sale.status)}</td>
        <td>${statusBadge(sale.payment_status)}</td>
        <td class="mono">${money(sale.total)}</td>
        <td class="mono">${money(sale.paid)}</td>
        <td>${
          ["open", "partially dispatched"].includes(sale.status)
            ? `${sale.status === "open" ? `<button class="button button-quiet button-small cancel-sale" data-sale-id="${sale.id}">Cancel</button> ` : ""}<button class="button button-secondary button-small dispatch-sale" data-sale-id="${sale.id}">Dispatch available</button>`
            : ""
        }</td>
      </tr>`,
          )
          .join("")
      : '<tr><td colspan="8" class="empty-cell">No sales have been recorded.</td></tr>';
  } catch (error) {
    table.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function dispatchAvailable(saleId) {
  const { sale } = await api(`/api/sales/${saleId}`);
  const items = sale.items.flatMap((item) => {
    const remaining = Number(item.quantity) - Number(item.dispatched_quantity);
    const available = item.package_id
      ? Number(item.available_quantity || 0)
      : Number(item.stock_quantity || 0);
    const dispatchQuantity = Math.min(remaining, available);
    return dispatchQuantity > 0
      ? [{ sale_item_id: item.id, quantity: dispatchQuantity }]
      : [];
  });
  if (!items.length) {
    toast("No ordered items are currently available to dispatch.", true);
    return;
  }
  const result = await api(`/api/sales/${saleId}/dispatch`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
  toast(result.message);
  await loadSales();
}

async function initSales() {
  const catalog = $("#catalog-grid");
  if (!catalog) return;
  let products = [];
  let cart = new Map();
  const vatRate = 0.12;
  $("#vat-rate-label").textContent = `${vatRate * 100}%`;

  function updateCart() {
    const entries = [...cart.values()];
    const subtotal = entries.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0,
    );
    const discount = Math.min(
      Math.max(0, Number($("#discount").value || 0)),
      subtotal,
    );
    const vat = (subtotal - discount) * vatRate;
    const total = subtotal - discount + vat;
    $("#cart-count").textContent = entries.reduce(
      (sum, item) => sum + item.quantity,
      0,
    );
    $("#cart-subtotal").textContent = money(subtotal);
    $("#cart-vat").textContent = money(vat);
    $("#cart-total").textContent = money(total);
    $("#discount").max = subtotal.toFixed(2);
    if (Number($("#discount").value || 0) > subtotal)
      $("#discount").value = subtotal.toFixed(2);
    $("#payment").max = total.toFixed(2);
    $("#payment").placeholder = total.toFixed(2);
    const cartNode = $("#cart-items");
    cartNode.innerHTML = entries.length
      ? entries
          .map(
            (item) => `
      <div class="cart-line">
        <div><div class="cart-line-name">${escapeHtml(item.name)}</div>
          <div class="cart-line-sub">${escapeHtml(item.sku)} · ${money(item.price)} each</div></div>
        <div class="cart-line-controls">
          <input class="quantity-input cart-quantity" type="number" min="1" max="999" value="${item.quantity}" data-key="${escapeHtml(item.key)}" aria-label="Quantity of ${escapeHtml(item.name)}">
          <button type="button" class="remove-line" data-remove="${escapeHtml(item.key)}" aria-label="Remove ${escapeHtml(item.name)}">×</button>
        </div>
        <div class="cart-line-sub">${item.kind === "package" ? "Package" : "Product"}</div>
        <strong class="mono">${money(item.price * item.quantity)}</strong>
      </div>`,
          )
          .join("")
      : '<div class="empty-cart">Add products from the catalog to get started.</div>';
  }

  function renderCatalog(filter = "") {
    const query = filter.toLowerCase();
    const filtered = products.filter((item) =>
      `${item.name} ${item.sku}`.toLowerCase().includes(query),
    );
    $("#catalog-count").textContent = `${filtered.length} items`;
    catalog.innerHTML = filtered.length
      ? filtered
          .map(
            (item) => `
      <article class="product-card">
        <div class="product-card-top"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.sku)} · ${escapeHtml(item.kind === "package" ? "Package" : item.category || "Product")}</small></div>
          ${
            item.kind === "product"
              ? statusBadge(
                  item.stock_quantity === 0
                    ? "Out of stock"
                    : item.stock_quantity <= item.reorder_level
                      ? "Low stock"
                      : "In stock",
                )
              : `<span class="status status-info">${Number(item.available_quantity || 0)} available</span>`
          }</div>
        <div class="product-card-bottom"><span class="product-price">${money(item.price)}</span>
          <button class="add-product" type="button" data-add="${escapeHtml(item.key)}" aria-label="Add ${escapeHtml(item.name)} to cart">+</button></div>
      </article>`,
          )
          .join("")
      : '<div class="empty-cart">No products match your search.</div>';
  }

  try {
    const result = await api("/api/products");
    products = [
      ...result.products.map((item) => ({
        ...item,
        key: `product-${item.id}`,
        kind: "product",
        price: Number(item.selling_price),
      })),
      ...result.packages.map((item) => ({
        ...item,
        key: `package-${item.id}`,
        kind: "package",
        price: Number(item.selling_price),
      })),
    ];
    renderCatalog();
  } catch (error) {
    catalog.innerHTML = `<div class="empty-cart">${escapeHtml(error.message)}</div>`;
  }

  catalog.addEventListener("click", (event) => {
    const button = event.target.closest("[data-add]");
    if (!button) return;
    const item = products.find((entry) => entry.key === button.dataset.add);
    if (!item) return;
    const current = cart.get(item.key);
    cart.set(item.key, { ...item, quantity: (current?.quantity || 0) + 1 });
    updateCart();
  });
  $("#catalog-search").addEventListener("input", (event) =>
    renderCatalog(event.target.value),
  );
  $("#cart-items").addEventListener("change", (event) => {
    const field = event.target.closest(".cart-quantity");
    if (!field) return;
    const item = cart.get(field.dataset.key);
    if (!item) return;
    const quantity = Math.min(
      999,
      Math.max(1, Number.parseInt(field.value, 10) || 1),
    );
    cart.set(item.key, { ...item, quantity });
    updateCart();
  });
  $("#cart-items").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;
    cart.delete(button.dataset.remove);
    updateCart();
  });
  $("#discount").addEventListener("input", updateCart);
  $("#clear-cart").addEventListener("click", () => {
    cart.clear();
    updateCart();
  });
  $("#checkout-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const entries = [...cart.values()];
    if (!entries.length)
      return toast("Add at least one product to the sale.", true);
    const form = event.currentTarget;
    const formData = serializeForm(form);
    const payment = Math.max(0, Number(formData.payment || 0));
    const subtotal = entries.reduce(
      (sum, item) => sum + item.price * item.quantity,
      0,
    );
    const discount = Math.min(
      Math.max(0, Number($("#discount").value || 0)),
      subtotal,
    );
    const total = (subtotal - discount) * (1 + vatRate);
    if (
      payment > total &&
      !window.confirm(
        `Payment exceeds the total by ${money(payment - total)}. Record the change?`,
      )
    )
      return;
    const payload = {
      customer: {
        name: formData.customer_name,
        tin: formData.tin,
        address: formData.address,
        business_style: formData.business_style,
      },
      customer_po_reference: formData.customer_po_reference,
      discount,
      payment,
      payment_method: formData.payment_method,
      items: entries.map((item) => ({
        ...(item.kind === "package"
          ? { package_id: item.id }
          : { product_id: item.id }),
        quantity: item.quantity,
      })),
    };
    try {
      const created = await api("/api/sales", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      try {
        await dispatchAvailable(created.id);
        toast(
          `Sale ${created.receipt_number} saved. Change: ${money(created.change)}.`,
        );
      } catch (dispatchError) {
        toast(
          `Sale ${created.receipt_number} saved, but dispatch needs attention: ${dispatchError.message}`,
          true,
        );
      }
      cart.clear();
      form.reset();
      $("#discount").value = "0";
      updateCart();
      await loadSales();
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#sales-table").addEventListener("click", async (event) => {
    const button = event.target.closest(".dispatch-sale");
    const cancel = event.target.closest(".cancel-sale");
    if (cancel) {
      if (
        !window.confirm(
          "Cancel this order before dispatch? Any collected payment will be refunded by an authorized administrator.",
        )
      )
        return;
      try {
        const result = await api(`/api/sales/${cancel.dataset.saleId}/cancel`, {
          method: "POST",
          body: "{}",
        });
        toast(result.message);
        await loadSales();
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    if (!button) return;
    button.disabled = true;
    try {
      await dispatchAvailable(button.dataset.saleId);
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  });
  $$('[data-refresh="sales"]').forEach((button) =>
    button.addEventListener("click", loadSales),
  );
  await loadSales();
}

async function initInventory() {
  const table = $("#inventory-table");
  if (!table) return;
  let products = [];
  let packages = [];

  function renderInventory(search = "") {
    const query = search.toLowerCase();
    const canManage = $("#inventory-panel")?.dataset.canManage === "true";
    const filtered = products.filter((item) =>
      `${item.name} ${item.sku} ${item.category} ${item.supplier || ""}`
        .toLowerCase()
        .includes(query),
    );
    $("#inventory-product-count").textContent = products.length;
    $("#inventory-low-count").textContent = products.filter(
      (item) =>
        Number(item.stock_quantity) > 0 &&
        Number(item.stock_quantity) <= Number(item.reorder_level),
    ).length;
    $("#inventory-out-count").textContent = products.filter(
      (item) => Number(item.stock_quantity) === 0,
    ).length;
    table.innerHTML = filtered.length
      ? filtered
          .map((item) => {
            const status =
              Number(item.stock_quantity) === 0
                ? "Out of stock"
                : Number(item.stock_quantity) <= Number(item.reorder_level)
                  ? "Low stock"
                  : "In stock";
            return `<tr><td><strong>${escapeHtml(item.name)}</strong><small class="table-sub mono">${escapeHtml(item.sku)}</small></td>
        <td>${escapeHtml(item.category)}</td><td>${escapeHtml(item.supplier || "—")}</td>
        <td class="mono">${money(item.selling_price)}</td><td class="mono">${money(item.acquisition_cost)}</td>
        <td class="mono">${item.stock_quantity}</td><td class="mono">${item.reorder_level}</td><td>${statusBadge(status)}</td>
        ${canManage ? `<td><button class="button button-quiet button-small edit-product" data-id="${item.id}">Edit</button> <button class="button button-quiet button-small deactivate-product" data-id="${item.id}">Deactivate</button></td>` : ""}</tr>`;
          })
          .join("")
      : `<tr><td colspan="${canManage ? 9 : 8}" class="empty-cell">No products match your search.</td></tr>`;
    const adjustProduct = $("#adjust-product");
    if (adjustProduct) {
      adjustProduct.innerHTML = products
        .map(
          (item) =>
            `<option value="${item.id}">${escapeHtml(item.sku)} · ${escapeHtml(item.name)} (${item.stock_quantity})</option>`,
        )
        .join("");
    }
    const packageProduct = $("#package-product");
    if (packageProduct) {
      packageProduct.innerHTML = products
        .map(
          (item) =>
            `<option value="${item.id}">${escapeHtml(item.sku)} · ${escapeHtml(item.name)}</option>`,
        )
        .join("");
    }
  }

  async function loadProducts() {
    try {
      const result = await api("/api/products");
      products = result.products;
      packages = result.packages;
      renderInventory($("#inventory-search")?.value || "");
      const options = `<option value="">Not assigned</option>${result.suppliers.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}`;
      const supplierSelect = $('#product-form [name="supplier_id"]');
      if (supplierSelect) supplierSelect.innerHTML = options;
      const editSupplier = $('#product-edit-form [name="supplier_id"]');
      if (editSupplier) editSupplier.innerHTML = options;
      const packageTable = $("#package-table");
      if (packageTable) {
        packageTable.innerHTML = packages.length
          ? packages
              .map(
                (item) => `
          <tr><td><strong>${escapeHtml(item.name)}</strong><small class="table-sub mono">${escapeHtml(item.sku)}</small></td>
            <td class="mono">${money(item.selling_price)}</td><td class="mono">${item.available_quantity ?? 0}</td>
            <td><button class="button button-quiet button-small deactivate-package" data-id="${item.id}">Deactivate</button></td></tr>`,
              )
              .join("")
          : '<tr><td colspan="4" class="empty-cell">No packages have been created.</td></tr>';
      }
    } catch (error) {
      table.innerHTML = `<tr><td colspan="9" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function loadMovements(query = "") {
    const history = $("#movement-table");
    if (!history) return;
    try {
      const result = await api(
        `/api/inventory/movements${query ? `?${query}` : ""}`,
      );
      history.innerHTML = result.movements.length
        ? result.movements
            .map(
              (item) => `
        <tr><td>${new Date(item.created_at).toLocaleString()}</td>
          <td><strong>${escapeHtml(item.name)}</strong><small class="table-sub mono">${escapeHtml(item.sku)}</small></td>
          <td>${statusBadge(item.movement_type)}</td><td class="mono">${item.quantity}</td>
          <td class="mono">${item.previous_balance} → ${item.resulting_balance}</td>
          <td>${escapeHtml(item.reason)}<small class="table-sub mono">${escapeHtml(item.reference)}</small></td>
          <td>${escapeHtml(item.staff)}</td></tr>`,
            )
            .join("")
        : '<tr><td colspan="7" class="empty-cell">No stock movements match these filters.</td></tr>';
    } catch (error) {
      history.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  $("#inventory-search")?.addEventListener("input", (event) =>
    renderInventory(event.target.value),
  );
  $("#movement-filter")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = new URLSearchParams(
      serializeForm(event.currentTarget),
    ).toString();
    loadMovements(query);
    $("#movement-export").href = `/api/inventory/movements/export?${query}`;
  });
  $("#product-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    try {
      await api("/api/products", {
        method: "POST",
        body: JSON.stringify(serializeForm(form)),
      });
      form.closest("dialog").close();
      form.reset();
      toast("Product added. Receive stock through a supplier purchase order.");
      await loadProducts();
    } catch (error) {
      toast(error.message, true);
    } finally {
      submitButton.disabled = false;
    }
  });
  $("#product-edit-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api(`/api/products/${form.elements.product_id.value}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: form.elements.name.value,
          category: form.elements.category.value,
          supplier_id: form.elements.supplier_id.value,
          selling_price: form.elements.selling_price.value,
          reorder_level: form.elements.reorder_level.value,
        }),
      });
      form.closest("dialog").close();
      toast("Product details updated.");
      await loadProducts();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#inventory-table")?.addEventListener("click", async (event) => {
    const edit = event.target.closest(".edit-product");
    const deactivate = event.target.closest(".deactivate-product");
    if (edit) {
      const item = products.find(
        (entry) => entry.id === Number(edit.dataset.id),
      );
      if (!item) return;
      const form = $("#product-edit-form");
      form.elements.product_id.value = item.id;
      form.elements.name.value = item.name;
      form.elements.category.value =
        item.category === "Uncategorized" ? "" : item.category;
      form.elements.supplier_id.value = item.supplier_id || "";
      form.elements.selling_price.value = item.selling_price;
      form.elements.reorder_level.value = item.reorder_level;
      return showDialog("product-edit-dialog");
    }
    if (
      !deactivate ||
      !window.confirm(
        "Deactivate this product? Sales and stock history will be kept.",
      )
    )
      return;
    try {
      await api(`/api/products/${deactivate.dataset.id}/deactivate`, {
        method: "POST",
        body: "{}",
      });
      toast("Product deactivated.");
      await loadProducts();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#package-table")?.addEventListener("click", async (event) => {
    const button = event.target.closest(".deactivate-package");
    if (
      !button ||
      !window.confirm(
        "Deactivate this package? Transaction history will be kept.",
      )
    )
      return;
    try {
      await api(`/api/packages/${button.dataset.id}/deactivate`, {
        method: "POST",
        body: "{}",
      });
      toast("Package deactivated.");
      await loadProducts();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#package-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    const components = $$(".package-component", $("#package-components")).map(
      (row) => ({
        product_id: row.querySelector('[name="component_product"]').value,
        quantity: row.querySelector('[name="component_quantity"]').value,
      }),
    );
    try {
      await api("/api/packages", {
        method: "POST",
        body: JSON.stringify({
          sku: form.elements.sku.value,
          name: form.elements.name.value,
          selling_price: form.elements.selling_price.value,
          components,
        }),
      });
      form.closest("dialog").close();
      form.reset();
      $("#package-components").innerHTML = "";
      toast("Package added to the catalog.");
      await loadProducts();
    } catch (error) {
      toast(error.message, true);
    } finally {
      submitButton.disabled = false;
    }
  });
  $("#add-package-component")?.addEventListener("click", () => {
    const row = document.createElement("div");
    row.className = "package-component form-grid";
    row.innerHTML = `<label>Product<select name="component_product" required>${products.map((item) => `<option value="${item.id}">${escapeHtml(item.sku)} · ${escapeHtml(item.name)}</option>`).join("")}</select></label>
      <label>Units per package<input name="component_quantity" type="number" min="1" value="1" required></label>`;
    $("#package-components").append(row);
  });
  $("#adjustment-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/inventory/adjustments", {
        method: "POST",
        body: JSON.stringify(serializeForm(event.currentTarget)),
      });
      event.currentTarget.reset();
      toast("Adjustment request sent to an administrator for approval.");
    } catch (error) {
      toast(error.message, true);
    }
  });
  await loadProducts();
  await loadMovements();
}

async function initSuppliers() {
  const supplierTable = $("#supplier-table");
  if (!supplierTable) return;
  let suppliers = [];
  let products = [];

  async function loadSuppliers() {
    try {
      const result = await api("/api/suppliers");
      suppliers = result.suppliers;
      const query = ($("#supplier-search")?.value || "").trim().toLowerCase();
      const visible = suppliers.filter((supplier) =>
        `${supplier.name} ${supplier.contact_person} ${supplier.phone} ${supplier.email}`
          .toLowerCase()
          .includes(query),
      );
      supplierTable.innerHTML = visible.length
        ? visible
            .map(
              (supplier) => `
        <tr><td><strong>${escapeHtml(supplier.name)}</strong><small class="table-sub">${escapeHtml(supplier.address || "")}</small></td>
          <td>${escapeHtml(supplier.contact_person || "—")}</td>
          <td>${escapeHtml(supplier.phone || "—")}<small class="table-sub">${escapeHtml(supplier.email || "")}</small></td>
          <td class="mono">${supplier.purchase_orders}</td>
          <td><button class="button button-quiet button-small edit-supplier" data-id="${supplier.id}">Edit</button> <button class="button button-quiet button-small deactivate-supplier" data-id="${supplier.id}">Deactivate</button></td></tr>`,
            )
            .join("")
        : `<tr><td colspan="5" class="empty-cell">${suppliers.length ? "No suppliers match your search." : "Add a supplier to begin."}</td></tr>`;
      $("#po-supplier").innerHTML = suppliers
        .map(
          (item) =>
            `<option value="${item.id}">${escapeHtml(item.name)}</option>`,
        )
        .join("");
    } catch (error) {
      supplierTable.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function loadOrders() {
    const table = $("#purchase-order-table");
    try {
      const result = await api("/api/purchase-orders");
      table.innerHTML = result.orders.length
        ? result.orders
            .map(
              (order) => `
        <tr><td><strong class="mono">${escapeHtml(order.order_number)}</strong></td>
          <td>${escapeHtml(order.supplier)}</td><td>${escapeHtml(order.expected_date || "—")}</td>
          <td>${statusBadge(order.status)}</td><td class="mono">${order.units_open}</td>
          <td class="action-cell">
            ${order.status === "draft" ? `<button class="button button-secondary button-small po-action" data-id="${order.id}" data-status="ordered">Submit</button>` : ""}
            ${["ordered", "partially received"].includes(order.status) ? `<button class="button button-secondary button-small receive-order" data-id="${order.id}" data-number="${escapeHtml(order.order_number)}">Receive</button>` : ""}
            ${["draft", "ordered", "partially received"].includes(order.status) ? `<button class="button button-quiet button-small po-action" data-id="${order.id}" data-status="cancelled">Cancel</button>` : ""}
          </td></tr>`,
            )
            .join("")
        : '<tr><td colspan="6" class="empty-cell">No purchase orders recorded yet.</td></tr>';
    } catch (error) {
      table.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  try {
    const result = await api("/api/products");
    products = result.products;
    $("#po-product").innerHTML = products
      .map(
        (item) =>
          `<option value="${item.id}">${escapeHtml(item.sku)} · ${escapeHtml(item.name)}</option>`,
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
  $("#supplier-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    try {
      await api("/api/suppliers", {
        method: "POST",
        body: JSON.stringify(serializeForm(form)),
      });
      form.closest("dialog").close();
      form.reset();
      toast("Supplier saved.");
      await loadSuppliers();
    } catch (error) {
      toast(error.message, true);
    } finally {
      submitButton.disabled = false;
    }
  });
  $("#supplier-search")?.addEventListener("input", loadSuppliers);
  $("#supplier-table").addEventListener("click", async (event) => {
    const edit = event.target.closest(".edit-supplier");
    const button = event.target.closest(".deactivate-supplier");
    if (edit) {
      const supplier = suppliers.find(
        (item) => item.id === Number(edit.dataset.id),
      );
      if (!supplier) return;
      const form = $("#supplier-edit-form");
      form.elements.supplier_id.value = supplier.id;
      for (const field of [
        "name",
        "contact_person",
        "phone",
        "email",
        "address",
        "notes",
      ]) {
        form.elements[field].value = supplier[field] || "";
      }
      return showDialog("supplier-edit-dialog");
    }
    if (
      !button ||
      !window.confirm(
        "Deactivate this supplier? Historical purchase records will be kept.",
      )
    )
      return;
    try {
      await api(`/api/suppliers/${button.dataset.id}/deactivate`, {
        method: "POST",
        body: "{}",
      });
      toast("Supplier deactivated.");
      await loadSuppliers();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#supplier-edit-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const data = serializeForm(form);
      await api(`/api/suppliers/${data.supplier_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: data.name,
          contact_person: data.contact_person,
          phone: data.phone,
          email: data.email,
          address: data.address,
          notes: data.notes,
        }),
      });
      form.closest("dialog").close();
      toast("Supplier details updated.");
      await loadSuppliers();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#purchase-order-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/purchase-orders", {
        method: "POST",
        body: JSON.stringify({
          supplier_id: event.currentTarget.elements.supplier_id.value,
          expected_date: event.currentTarget.elements.expected_date.value,
          status: event.currentTarget.elements.status.value,
          items: [
            {
              product_id: event.currentTarget.elements.product_id.value,
              quantity: event.currentTarget.elements.quantity.value,
              unit_cost: event.currentTarget.elements.unit_cost.value,
            },
          ],
        }),
      });
      event.currentTarget.reset();
      toast(
        "Purchase order created. Receive accepted quantities when the delivery arrives.",
      );
      await loadOrders();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#purchase-order-table").addEventListener("click", async (event) => {
    const statusAction = event.target.closest(".po-action");
    if (statusAction) {
      if (
        statusAction.dataset.status === "cancelled" &&
        !window.confirm(
          "Cancel this purchase order? Received stock will remain recorded.",
        )
      )
        return;
      try {
        const result = await api(
          `/api/purchase-orders/${statusAction.dataset.id}`,
          {
            method: "PATCH",
            body: JSON.stringify({ status: statusAction.dataset.status }),
          },
        );
        toast(result.message);
        await loadOrders();
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    const button = event.target.closest(".receive-order");
    if (!button) return;
    try {
      const { order } = await api(`/api/purchase-orders/${button.dataset.id}`);
      $("#receive-order-id").value = order.id;
      $("#receive-order-label").textContent =
        `${order.order_number} · ${order.supplier}`;
      $("#receive-lines").innerHTML = order.items
        .filter((item) => item.quantity_open > 0)
        .map(
          (item) => `
        <div class="receive-line" data-item-id="${item.id}">
          <div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.sku)} · ${item.quantity_open} units open</small></div>
          <label>Accepted qty<input name="quantity" type="number" min="0" max="${item.quantity_open}" value="${item.quantity_open}"></label>
          <label>Unit cost<input name="unit_cost" type="number" min="0" step="0.01" value="${item.expected_unit_cost}"></label>
        </div>`,
        )
        .join("");
      $("#receive-form").dataset.orderId = order.id;
      showDialog("receive-dialog");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#receive-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const items = $$(".receive-line", $("#receive-lines"))
      .map((row) => ({
        item_id: row.dataset.itemId,
        quantity: row.querySelector('[name="quantity"]').value,
        unit_cost: row.querySelector('[name="unit_cost"]').value,
      }))
      .filter((item) => Number(item.quantity) > 0);
    if (!items.length)
      return toast("Enter at least one accepted quantity.", true);
    try {
      const result = await api(
        `/api/purchase-orders/${form.dataset.orderId}/receive`,
        {
          method: "POST",
          body: JSON.stringify({
            reference: form.elements.reference.value,
            items,
          }),
        },
      );
      form.closest("dialog").close();
      form.reset();
      toast(result.message);
      await loadOrders();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $$('[data-refresh="purchase-orders"]').forEach((button) =>
    button.addEventListener("click", loadOrders),
  );
  await loadSuppliers();
  await loadOrders();
}

async function initReports() {
  const resultNode = $("#report-result");
  if (!resultNode) return;
  const form = $("#report-filter");
  const today = localDate();
  form.elements.date.value = today;
  form.elements.month.value = today.slice(0, 7);
  const firstDay = `${today.slice(0, 7)}-01`;
  form.elements.from.value = firstDay;
  form.elements.to.value = today;
  let reportType = "daily";

  function configureFilters() {
    $(".report-date-label").classList.toggle("hidden", reportType !== "daily");
    $(".report-month-label").classList.toggle(
      "hidden",
      reportType !== "monthly",
    );
    $$(".range-field").forEach((field) =>
      field.classList.toggle(
        "hidden",
        !["best", "profit"].includes(reportType),
      ),
    );
    $(".category-field").classList.toggle(
      "hidden",
      !["best", "profit"].includes(reportType),
    );
  }

  function kpi(label, value) {
    return `<div class="report-kpi"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`;
  }

  async function runReport() {
    resultNode.innerHTML = '<div class="loading-state">Preparing report…</div>';
    try {
      const params = new URLSearchParams({ type: reportType });
      if (reportType === "daily") params.set("date", form.elements.date.value);
      if (reportType === "monthly")
        params.set("month", form.elements.month.value);
      if (["best", "profit"].includes(reportType)) {
        params.set("from", form.elements.from.value);
        params.set("to", form.elements.to.value);
        if (form.elements.category_id.value)
          params.set("category_id", form.elements.category_id.value);
      }
      const data = await api(`/api/reports?${params}`);
      if (reportType === "daily") {
        const summary = data.summary;
        resultNode.innerHTML = `<div class="section-heading"><div><span class="eyebrow">DAILY SALES REPORT</span><h2>${escapeHtml(data.date)}</h2></div><span class="status status-info">${summary.transaction_count} transactions</span></div>
          <div class="report-kpis">${kpi("Sales value", money(summary.sales_value))}${kpi("Discounts", money(summary.discounts))}${kpi("Returns / refunds", money(summary.refunds))}${kpi("VAT", money(summary.vat))}${kpi("Net sales · VAT exclusive", money(summary.net_sales))}${kpi("Payments received today", money(summary.payments))}${kpi("Outstanding balance", money(summary.outstanding))}${kpi("Net quantities sold", summary.quantities_sold)}</div>
          <p class="muted">Payments received and sales revenue are reported separately.</p>`;
      } else if (reportType === "monthly") {
        const t = data.totals;
        resultNode.innerHTML = `<div class="section-heading"><div><span class="eyebrow">MONTHLY SALES REPORT</span><h2>${escapeHtml(data.month)}</h2></div></div>
          <div class="report-kpis">${kpi("Sales before discounts", money(t.sales_value))}${kpi("Discounts", money(t.discounts))}${kpi("Refunds", money(t.refunds))}${kpi("Net sales · before VAT", money(t.net_sales))}${kpi("VAT", money(t.vat))}${kpi("Payments collected", money(t.payments))}${kpi("Outstanding balance", money(t.outstanding))}${kpi("Previous month sales", money(data.previous_month_sales))}</div>
          <div class="table-wrap"><table><thead><tr><th>Date</th><th>Transactions</th><th>Sales</th><th>Discounts</th><th>Refunds</th><th>VAT</th><th>Net sales</th></tr></thead><tbody>${data.days.map((row) => `<tr><td>${escapeHtml(row.day)}</td><td>${row.transactions}</td><td class="mono">${money(row.sales_value)}</td><td class="mono">${money(row.discounts)}</td><td class="mono">${money(row.refunds)}</td><td class="mono">${money(row.vat)}</td><td class="mono">${money(row.net_sales)}</td></tr>`).join("") || '<tr><td colspan="7" class="empty-cell">No dispatched sales in this month.</td></tr>'}</tbody></table></div>`;
      } else if (reportType === "best") {
        resultNode.innerHTML = `<div class="section-heading"><div><span class="eyebrow">BEST-SELLING PRODUCTS</span><h2>${escapeHtml(data.from_date)} – ${escapeHtml(data.to_date)}</h2></div></div>
          <div class="table-wrap"><table><thead><tr><th>Rank</th><th>Product</th><th>Category</th><th>Net quantity sold</th><th>Net sales</th></tr></thead><tbody>${data.products.map((item, index) => `<tr><td>${index + 1}</td><td><strong>${escapeHtml(item.name)}</strong><small class="table-sub mono">${escapeHtml(item.sku)}</small></td><td>${escapeHtml(item.category)}</td><td class="mono">${item.net_quantity}</td><td class="mono">${money(item.net_sales)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty-cell">No sales in this date range.</td></tr>'}</tbody></table></div>`;
      } else {
        const summary = data.summary;
        resultNode.innerHTML = `<div class="section-heading"><div><span class="eyebrow">GROSS PROFIT REPORT</span><h2>${escapeHtml(data.from_date)} – ${escapeHtml(data.to_date)}</h2></div></div>
          <div class="report-kpis">${kpi("Net sales · VAT exclusive", money(summary.net_sales))}${kpi("Cost of goods sold", money(summary.cost_of_goods_sold))}${kpi("Gross profit", money(summary.gross_profit))}${kpi("Gross margin", summary.gross_margin === null ? "N/A" : `${Number(summary.gross_margin).toFixed(2)}%`)}${kpi("Recorded inventory losses", money(summary.inventory_losses))}${kpi("Gross profit after losses", money(summary.after_losses))}</div>
          <p class="muted">Dispatch-time costs are used; returned saleable goods reverse their original cost.</p>`;
      }
    } catch (error) {
      resultNode.innerHTML = `<div class="notice notice-error">${escapeHtml(error.message)}</div>`;
    }
    const exportParams = new URLSearchParams({ type: reportType });
    if (reportType === "daily")
      exportParams.set("date", form.elements.date.value);
    if (reportType === "monthly")
      exportParams.set("month", form.elements.month.value);
    if (["best", "profit"].includes(reportType)) {
      exportParams.set("from", form.elements.from.value);
      exportParams.set("to", form.elements.to.value);
      if (form.elements.category_id.value)
        exportParams.set("category_id", form.elements.category_id.value);
    }
    $("#export-report").href = `/api/reports/export?${exportParams}`;
  }

  try {
    const { categories } = await api("/api/categories");
    form.elements.category_id.innerHTML =
      '<option value="">All categories</option>' +
      categories
        .map(
          (item) =>
            `<option value="${item.id}">${escapeHtml(item.name)}</option>`,
        )
        .join("");
  } catch (error) {
    toast(error.message, true);
  }
  $$(".report-tab").forEach((button) =>
    button.addEventListener("click", () => {
      reportType = button.dataset.report;
      $$(".report-tab").forEach((tab) => {
        const active = tab === button;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      configureFilters();
      runReport();
    }),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runReport();
  });
  configureFilters();
  await runReport();
}

async function initReceipts() {
  const table = $("#receipt-table");
  if (!table) return;

  async function loadReceipts(search = "") {
    try {
      const data = await api(`/api/receipts?q=${encodeURIComponent(search)}`);
      table.innerHTML = data.receipts.length
        ? data.receipts
            .map(
              (receipt) => `
        <tr><td><strong class="mono">${escapeHtml(receipt.receipt_number)}</strong></td>
          <td>${new Date(receipt.created_at).toLocaleString()}</td><td>${escapeHtml(receipt.customer)}</td>
          <td>${statusBadge(receipt.status)}</td><td class="mono">${money(receipt.total)}</td>
          <td><button class="button button-secondary button-small view-receipt" data-id="${receipt.id}">View / print</button></td></tr>`,
            )
            .join("")
        : '<tr><td colspan="6" class="empty-cell">No receipts match your search.</td></tr>';
    } catch (error) {
      table.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  $("#receipt-search").addEventListener("submit", (event) => {
    event.preventDefault();
    loadReceipts(event.currentTarget.elements.q.value);
  });
  table.addEventListener("click", async (event) => {
    const button = event.target.closest(".view-receipt");
    if (!button) return;
    try {
      const { receipt } = await api(`/api/receipts/${button.dataset.id}`);
      const items = receipt.items
        .map(
          (item) =>
            `<tr><td>${escapeHtml(item.name)}<small class="table-sub">${escapeHtml(item.sku)} · ${item.dispatched_quantity} / ${item.quantity} dispatched · ${item.returned_quantity} returned</small></td><td class="mono">${item.quantity}</td><td class="mono">${money(item.unit_price)}</td><td class="mono">${money(item.line_total)}</td></tr>`,
        )
        .join("");
      const payments = receipt.payments
        .map(
          (payment) =>
            `<div><span>${escapeHtml(payment.method)} · ${escapeHtml(new Date(payment.received_at).toLocaleDateString())}</span><strong>${money(payment.amount)}</strong></div>`,
        )
        .join("");
      const refunds = receipt.returns
        .map(
          (item) =>
            `<div><span>Refund ${escapeHtml(item.reference)}</span><strong>−${money(item.refund_amount)}</strong></div>`,
        )
        .join("");
      const canRefund = $("#receipt-dialog").dataset.canRefund === "true";
      const returnForm =
        canRefund && receipt.status !== "refunded"
          ? `<section class="return-panel">
        <div class="section-heading"><div><span class="eyebrow">INSPECTED RETURNS</span><h2>Record a return / refund</h2></div></div>
        <form class="return-form" data-sale-id="${receipt.id}">
          <label>Reason<input name="reason" required maxlength="250"></label>
          <div class="table-wrap"><table><thead><tr><th>Sale line</th><th>Dispatched</th><th>Return qty</th><th>Saleable qty</th></tr></thead><tbody>
            ${receipt.items
              .filter(
                (item) => item.dispatched_quantity > item.returned_quantity,
              )
              .map(
                (item) => `<tr>
              <td>${escapeHtml(item.name)}</td><td>${item.dispatched_quantity - item.returned_quantity}</td>
              <td><input name="return_${item.id}" type="number" min="0" max="${item.dispatched_quantity - item.returned_quantity}" value="0" aria-label="Return quantity for ${escapeHtml(item.name)}"></td>
              <td><input name="saleable_${item.id}" type="number" min="0" value="0" aria-label="Saleable return quantity for ${escapeHtml(item.name)}"></td>
            </tr>`,
              )
              .join("")}
          </tbody></table></div>
          <button class="button button-danger" type="submit">Approve return &amp; refund</button>
        </form></section>`
          : "";
      $("#receipt-detail").innerHTML = `<article class="receipt-paper">
        <header class="receipt-head"><div><h2>Optimum Business Products</h2><small>Inventory Tracking &amp; Sales Management</small><small>1808 Cityland Pasong Tamo Tower, Makati City</small></div><div class="mono">${escapeHtml(receipt.receipt_number)}</div></header>
        <div class="receipt-meta"><span>Customer</span><strong>${escapeHtml(receipt.customer_name || "Walk-in customer")}</strong>
          <span>Date</span><strong>${escapeHtml(new Date(receipt.created_at).toLocaleString())}</strong>
          <span>Customer PO reference</span><strong>${escapeHtml(receipt.customer_po_reference || "—")}</strong>
          <span>Cashier</span><strong>${escapeHtml(receipt.cashier || "—")}</strong>
          ${receipt.tin ? `<span>TIN</span><strong>${escapeHtml(receipt.tin)}</strong>` : ""}
          ${receipt.address ? `<span>Address</span><strong>${escapeHtml(receipt.address)}</strong>` : ""}</div>
        <div class="table-wrap"><table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead><tbody>${items}</tbody></table></div>
        <div class="receipt-total"><div><span>Subtotal</span><strong>${money(receipt.subtotal)}</strong></div><div><span>Discount</span><strong>−${money(receipt.discount)}</strong></div><div><span>VAT</span><strong>${money(receipt.vat_amount)}</strong></div><div class="grand-total"><span>Total</span><strong>${money(receipt.total)}</strong></div>${payments}${refunds}</div>
        <div class="receipt-footer">Thank you for your business.</div>
      </article>${returnForm}`;
      showDialog("receipt-dialog");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#receipt-detail").addEventListener("submit", async (event) => {
    const form = event.target.closest(".return-form");
    if (!form) return;
    event.preventDefault();
    const elements = [...form.elements];
    const items = elements
      .filter(
        (field) => field.name.startsWith("return_") && Number(field.value) > 0,
      )
      .map((field) => {
        const saleItemId = field.name.slice("return_".length);
        const quantity = Number(field.value);
        const saleable = Number(
          form.elements[`saleable_${saleItemId}`].value || 0,
        );
        return {
          sale_item_id: saleItemId,
          quantity,
          saleable_quantity: saleable,
        };
      });
    if (!items.length)
      return toast("Enter a return quantity for at least one sale line.", true);
    if (items.some((item) => item.saleable_quantity > item.quantity)) {
      return toast(
        "Saleable quantity cannot exceed the returned quantity.",
        true,
      );
    }
    if (!window.confirm("Approve this return and issue the calculated refund?"))
      return;
    try {
      const result = await api(`/api/sales/${form.dataset.saleId}/returns`, {
        method: "POST",
        body: JSON.stringify({ reason: form.elements.reason.value, items }),
      });
      toast(`${result.message} Refund: ${money(result.refund)}.`);
      $("#receipt-dialog").close();
      await loadReceipts();
    } catch (error) {
      toast(error.message, true);
    }
  });
  await loadReceipts();
}

async function initAdmin() {
  if (!$("#user-form")) return;
  const data = await api("/api/admin/summary");
  const roleOptions = data.roles
    .map(
      (role) => `<option value="${role.id}">${escapeHtml(role.name)}</option>`,
    )
    .join("");
  $("#new-user-role").innerHTML = roleOptions;
  $("#users-table").innerHTML = data.users.length
    ? data.users
        .map(
          (user) => `
    <tr data-user-id="${user.id}" data-active="${user.active}">
      <td><strong>${escapeHtml(user.display_name)}</strong></td><td class="mono">${escapeHtml(user.username)}</td>
      <td><select class="user-role" aria-label="Role for ${escapeHtml(user.display_name)}">${data.roles
        .map(
          (role) =>
            `<option value="${role.id}" ${role.id === user.role_id ? "selected" : ""}>${escapeHtml(role.name)}</option>`,
        )
        .join("")}</select></td>
      <td>${user.active ? '<span class="status status-success">Active</span>' : '<span class="status status-neutral">Inactive</span>'}</td>
      <td><button class="button button-quiet button-small user-save" type="button">Save access</button> <button class="button button-quiet button-small user-toggle" type="button">${user.active ? "Deactivate" : "Activate"}</button></td>
    </tr>`,
        )
        .join("")
    : '<tr><td colspan="5" class="empty-cell">No staff accounts yet.</td></tr>';

  const allPermissions = [
    "products.read",
    "products.manage",
    "sales.create",
    "sales.read",
    "receipts.read",
    "inventory.read",
    "inventory.adjust.submit",
    "stock.adjustments.approve",
    "suppliers.manage",
    "restocking.manage",
    "reports.read",
    "reports.export",
    "users.manage",
    "roles.manage",
    "backup.manage",
    "refunds.approve",
  ];
  $("#role-grid").innerHTML = data.roles
    .map(
      (role) => `
    <article class="role-card"><div class="role-title"><strong>${escapeHtml(role.name)}</strong>
      ${role.name === "Administrator / Owner" ? '<span class="status status-danger">Full access</span>' : ""}</div>
      <div class="permission-list">${role.permissions
        .map(
          (permission) =>
            `<span class="permission-chip">${permission === "*" ? "All permissions" : escapeHtml(permission)}</span>`,
        )
        .join("")}</div>
      ${
        role.name !== "Administrator / Owner"
          ? `<details><summary>Edit permissions</summary>
        <form class="role-form" data-role-id="${role.id}"><div class="permission-checks">${allPermissions
          .map(
            (permission) =>
              `<label><input type="checkbox" value="${permission}" ${role.permissions.includes(permission) ? "checked" : ""}> ${permission}</label>`,
          )
          .join("")}</div>
          <button class="button button-secondary button-small" type="submit">Save role permissions</button>
        </form></details>`
          : ""
      }
    </article>`,
    )
    .join("");
  $("#adjustment-approvals").innerHTML = data.adjustments.length
    ? data.adjustments
        .map(
          (item) => `
    <tr data-adjustment-id="${item.id}"><td>${new Date(item.created_at).toLocaleString()}</td>
      <td><strong>${escapeHtml(item.product)}</strong><small class="table-sub mono">${escapeHtml(item.sku)}</small></td>
      <td>${escapeHtml(item.movement_type)}</td><td class="mono">${item.quantity_change}</td>
      <td>${escapeHtml(item.reason)}</td><td>${escapeHtml(item.requester || "—")}</td>
      <td><button class="button button-primary button-small approve-adjustment" type="button">Approve</button>
      <button class="button button-quiet button-small reject-adjustment" type="button">Reject</button></td></tr>`,
        )
        .join("")
    : '<tr><td colspan="7" class="empty-cell">No stock corrections are waiting for approval.</td></tr>';
  $("#backup-list").innerHTML = data.backups.length
    ? data.backups
        .map(
          (backup) =>
            `<option value="${escapeHtml(backup.filename)}">${escapeHtml(backup.filename)} · ${Math.round(backup.size / 1024)} KB</option>`,
        )
        .join("")
    : '<option value="">No backup files found</option>';
  $("#backup-log-table").innerHTML = data.backup_logs.length
    ? data.backup_logs
        .map(
          (log) => `
    <tr><td class="mono">${escapeHtml(log.filename)}</td><td>${new Date(log.created_at).toLocaleString()}</td>
      <td>${escapeHtml(log.actor)}</td><td>${statusBadge(log.status)}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="4" class="empty-cell">No backup activity recorded.</td></tr>';
  $("#activity-list").innerHTML = data.audit.length
    ? data.audit
        .map(
          (entry) => `
    <div class="activity-item"><span class="activity-mark"></span><div>
      <strong>${escapeHtml(entry.action)} ${escapeHtml(entry.record_type)}</strong>
      <small>${escapeHtml(entry.actor)} · ${new Date(entry.created_at).toLocaleString()}</small>
      ${entry.details ? `<small>${escapeHtml(entry.details)}</small>` : ""}
    </div></div>`,
        )
        .join("")
    : '<div class="empty-inline"><div><strong>No activity recorded</strong><small>Administrative events will be listed here.</small></div></div>';

  $("#user-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify(serializeForm(event.currentTarget)),
      });
      toast("Staff account created.");
      location.reload();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#users-table")?.addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-user-id]");
    if (!row) return;
    const payload = { role_id: row.querySelector(".user-role").value };
    const active = row.dataset.active === "true";
    if (event.target.closest(".user-toggle")) {
      payload.active = !active;
      if (
        active &&
        !window.confirm(
          "Deactivate this account? It will no longer be able to sign in.",
        )
      )
        return;
    } else if (event.target.closest(".user-save")) {
      const displayName = window.prompt(
        "Display name:",
        row.querySelector("td strong").textContent,
      );
      if (displayName === null) return;
      payload.display_name = displayName;
      const newPassword = window.prompt(
        "Leave blank to keep the current password, or enter a new password (12+ characters):",
      );
      if (newPassword) payload.password = newPassword;
    } else return;
    try {
      await api(`/api/users/${row.dataset.userId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      toast("Staff access updated.");
      location.reload();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $$(".role-form").forEach((form) =>
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const permissions = $$('input[type="checkbox"]:checked', form).map(
        (input) => input.value,
      );
      try {
        await api(`/api/roles/${form.dataset.roleId}`, {
          method: "PUT",
          body: JSON.stringify({ permissions }),
        });
        toast("Role permissions updated.");
        location.reload();
      } catch (error) {
        toast(error.message, true);
      }
    }),
  );
  $("#adjustment-approvals")?.addEventListener("click", async (event) => {
    const approve = event.target.closest(".approve-adjustment");
    const reject = event.target.closest(".reject-adjustment");
    if (!approve && !reject) return;
    const row = event.target.closest("[data-adjustment-id]");
    const decision = approve ? "approve" : "reject";
    if (
      !window.confirm(
        `${decision === "approve" ? "Approve" : "Reject"} this stock adjustment?`,
      )
    )
      return;
    try {
      await api(
        `/api/inventory/adjustments/${row.dataset.adjustmentId}/review`,
        {
          method: "POST",
          body: JSON.stringify({ decision }),
        },
      );
      toast(`Adjustment ${decision}d.`);
      location.reload();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#backup-now")?.addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      const data = await api("/api/backups", { method: "POST", body: "{}" });
      toast(data.message);
      location.reload();
    } catch (error) {
      toast(error.message, true);
      event.currentTarget.disabled = false;
    }
  });
  $("#restore-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = serializeForm(event.currentTarget);
    if (payload.confirmation !== "RESTORE")
      return toast("Type RESTORE exactly to continue.", true);
    if (
      !window.confirm(
        `This will replace the active database from ${payload.filename}. Continue?`,
      )
    )
      return;
    try {
      const result = await api("/api/backups/restore", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      toast(result.message);
      location.reload();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const session = await api("/api/session");
    csrfToken = session.csrf_token;
    currentUser = session.user;
    const pageName = document.body.dataset.page;
    if (pageName === "login") {
      await initLogin();
      return;
    }
    if (!currentUser) {
      location.href = "/login";
      return;
    }
    const pageContent = $("#page-content");
    showPageLayout(pageContent, pageName);
    await Promise.all([
      initDashboard(),
      initSales(),
      initInventory(),
      initSuppliers(),
      initReports(),
      initReceipts(),
      initAdmin(),
    ]);
  } catch (error) {
    toast(error.message, true);
  }
});
