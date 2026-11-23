const menuButton = document.querySelector(".store-menu-toggle");
const navigation = document.querySelector("#store-navigation");

menuButton?.addEventListener("click", () => {
  const expanded = menuButton.getAttribute("aria-expanded") === "true";
  menuButton.setAttribute("aria-expanded", String(!expanded));
  menuButton.setAttribute(
    "aria-label",
    expanded ? "Open navigation menu" : "Close navigation menu",
  );
  navigation.classList.toggle("is-open", !expanded);
});

navigation?.addEventListener("click", (event) => {
  if (!event.target.closest("a")) return;
  menuButton?.setAttribute("aria-expanded", "false");
  menuButton?.setAttribute("aria-label", "Open navigation menu");
  navigation.classList.remove("is-open");
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href]");
  if (
    !link ||
    link.target === "_blank" ||
    link.hasAttribute("download") ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }

  const destination = new URL(link.href, window.location.href);
  if (destination.origin !== window.location.origin) return;

  const current = new URL(window.location.href);
  const samePage =
    destination.pathname === current.pathname &&
    destination.search === current.search;
  if (samePage && destination.hash) return;
  if (samePage) {
    event.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  if (
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    document.body.classList.contains("store-page-leaving")
  ) {
    return;
  }

  event.preventDefault();
  document.body.classList.add("store-page-leaving");
  window.setTimeout(() => window.location.assign(destination.href), 180);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || menuButton?.getAttribute("aria-expanded") !== "true") {
    return;
  }
  menuButton?.setAttribute("aria-expanded", "false");
  menuButton?.setAttribute("aria-label", "Open navigation menu");
  navigation.classList.remove("is-open");
  menuButton?.focus();
});

document.querySelector("#newsletter-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  document.querySelector("#newsletter-message").textContent =
    "Sign-ups are not connected in this demo. Your email has not been saved.";
});

document.querySelector("#store-year").textContent = new Date().getFullYear();

const backgroundMusic = document.querySelector("#background-music");
const musicToggle = document.querySelector("#music-toggle");
const musicStatus = document.querySelector("#music-status");

if (backgroundMusic && musicToggle && musicStatus) {
  function updateMusicControl(isPlaying, label) {
    musicToggle.setAttribute("aria-pressed", String(isPlaying));
    musicToggle.setAttribute("aria-label", label);
    musicToggle.title = label;
  }

  musicToggle.addEventListener("click", async () => {
    musicStatus.textContent = "";
    if (!backgroundMusic.paused && !backgroundMusic.muted) {
      backgroundMusic.muted = true;
      updateMusicControl(false, "Unmute background music");
      return;
    }

    backgroundMusic.muted = false;
    try {
      await backgroundMusic.play();
      updateMusicControl(true, "Mute background music");
    } catch (error) {
      backgroundMusic.muted = true;
      updateMusicControl(false, "Try playing background music again");
      musicStatus.textContent = "Background music could not be played. Please try again.";
      console.error("Background music could not be played.", error);
    }
  });
}

const sampleProducts = [
  {
    name: "Business laptop",
    category: "Computers & parts",
    brand: "Dell",
    type: "computers",
    description: "Laptop example for office and business use.",
  },
  {
    name: "Business laptop",
    category: "Computers & parts",
    brand: "Acer",
    type: "computers",
    description: "Laptop example; models and specifications may vary.",
  },
  {
    name: "Computer parts & peripherals",
    category: "Computers & parts",
    brand: "Various brands",
    type: "computers",
    description: "Parts, peripherals, and networking product examples.",
  },
  {
    name: "Office printer",
    category: "Printers & machines",
    brand: "HP",
    type: "machines",
    description: "Printer example for everyday office printing.",
  },
  {
    name: "Ink tank printer",
    category: "Printers & machines",
    brand: "Epson",
    type: "machines",
    description: "Printer example from the business profile's listed product lines.",
  },
  {
    name: "Office printer",
    category: "Printers & machines",
    brand: "Canon",
    type: "machines",
    description: "Printer example; confirm current models with the store.",
  },
  {
    name: "Laser printer",
    category: "Printers & machines",
    brand: "Samsung",
    type: "machines",
    description: "Office printer example from a listed product brand.",
  },
  {
    name: "Office printer",
    category: "Printers & machines",
    brand: "Brother",
    type: "machines",
    description: "Office machine example; confirm current models with the store.",
  },
  {
    name: "Printer ribbons",
    category: "Business essentials",
    brand: "Fullmark",
    type: "supplies",
    description: "Ribbon and printing-supply example.",
  },
  {
    name: "Ink & toner cartridges",
    category: "Business essentials",
    brand: "Various brands",
    type: "supplies",
    description: "Ink and toner product examples for office printers.",
  },
  {
    name: "POS & barcode equipment",
    category: "Business essentials",
    brand: "Various brands",
    type: "supplies",
    description: "Point-of-sale and barcode product examples.",
  },
  {
    name: "Projectors & labellers",
    category: "Business essentials",
    brand: "Various brands",
    type: "supplies",
    description: "Office equipment examples listed in the company profile.",
  },
];

const productGrid = document.querySelector("#product-grid");
if (productGrid) {
  const searchInput = document.querySelector("#product-search");
  const categoryFilter = document.querySelector("#product-category");
  const brandFilter = document.querySelector("#product-brand");
  const resultCount = document.querySelector("#product-results");
  const emptyState = document.querySelector("#product-empty");

  for (const brand of [...new Set(sampleProducts.map((product) => product.brand))].sort()) {
    const option = document.createElement("option");
    option.value = brand;
    option.textContent = brand;
    brandFilter.append(option);
  }

  const requestedCategory = new URLSearchParams(window.location.search).get("category");
  if ([...categoryFilter.options].some((option) => option.value === requestedCategory)) {
    categoryFilter.value = requestedCategory;
  }

  function createProductCard(product) {
    const card = document.createElement("article");
    card.className = "store-product-card";

    const artwork = document.createElement("div");
    artwork.className = `store-product-art store-product-art-${product.type}`;
    const artworkLabel = document.createElement("span");
    artworkLabel.textContent = "SAMPLE PRODUCT";
    const brandMark = document.createElement("strong");
    brandMark.textContent = product.brand === "Various brands" ? "OBP" : product.brand;
    artwork.append(artworkLabel, brandMark);

    const details = document.createElement("div");
    details.className = "store-product-details";
    const category = document.createElement("span");
    category.className = "store-product-category";
    category.textContent = product.category;
    const name = document.createElement("h3");
    name.textContent = product.name;
    const description = document.createElement("p");
    description.textContent = product.description;
    const brand = document.createElement("p");
    brand.className = "store-product-brand";
    brand.textContent = `Brand: ${product.brand}`;
    const askLink = document.createElement("a");
    askLink.href = `mailto:optimumbusinessproducts@yahoo.com?subject=${encodeURIComponent(`Product inquiry: ${product.name}`)}`;
    askLink.textContent = "Ask about this item →";

    details.append(category, name, description, brand, askLink);
    card.append(artwork, details);
    return card;
  }

  function renderProducts() {
    const query = searchInput.value.trim().toLowerCase();
    const category = categoryFilter.value;
    const brand = brandFilter.value;
    const filteredProducts = sampleProducts.filter((product) => {
      const matchesQuery =
        !query ||
        [product.name, product.category, product.brand, product.description]
          .join(" ")
          .toLowerCase()
          .includes(query);
      return (
        matchesQuery &&
        (!category || product.category === category) &&
        (!brand || product.brand === brand)
      );
    });

    productGrid.replaceChildren(...filteredProducts.map(createProductCard));
    resultCount.textContent = `${filteredProducts.length} sample ${
      filteredProducts.length === 1 ? "item" : "items"
    }`;
    emptyState.hidden = filteredProducts.length > 0;
  }

  searchInput.addEventListener("input", renderProducts);
  categoryFilter.addEventListener("change", renderProducts);
  brandFilter.addEventListener("change", renderProducts);
  document
    .querySelector("#clear-product-filters")
    .addEventListener("click", () => {
      searchInput.value = "";
      categoryFilter.value = "";
      brandFilter.value = "";
      renderProducts();
      searchInput.focus();
    });

  renderProducts();
}
