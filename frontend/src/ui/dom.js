/**
 * Minimal DOM builder: h("div", {class: "x", onClick: fn, dataset: {...}}, child, ...).
 * Children may be strings, nodes, arrays, or null/false (skipped).
 */
export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    applyProp(element, key, value);
  }
  append(element, children);
  return element;
}

function applyProp(element, key, value) {
  if (value === null || value === undefined || value === false) return;
  if (key === "class") {
    element.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
  } else if (key === "dataset") {
    Object.assign(element.dataset, value);
  } else if (key === "style" && typeof value === "object") {
    for (const [name, styleValue] of Object.entries(value)) element.style.setProperty(name, styleValue);
  } else if (key.startsWith("on") && typeof value === "function") {
    element.addEventListener(key.slice(2).toLowerCase(), value);
  } else if (value === true) {
    element.setAttribute(key, "");
  } else {
    element.setAttribute(key, String(value));
  }
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function replaceChildren(parent, ...children) {
  parent.replaceChildren();
  return append(parent, children);
}

/** Icon exported from Figma (`assets/icons/<name>.svg`); decorative by default. */
export function icon(name, size, extraClass = "") {
  return h("img", {
    class: ["icon", extraClass],
    src: `assets/icons/${name}.svg`,
    width: size,
    height: size,
    alt: "",
    "aria-hidden": "true",
  });
}

/**
 * Collapses an element smoothly and then removes it.
 * The element must be (or be wrapped in) a `.collapsible` container.
 */
export function collapseAndRemove(element) {
  return new Promise((resolve) => {
    if (!element.isConnected) return resolve();
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      element.remove();
      resolve();
    };
    element.addEventListener("transitionend", (event) => {
      if (event.target === element && event.propertyName === "grid-template-rows") done();
    });
    // Safety net: reduced motion or display:none parents never fire transitionend.
    setTimeout(done, 600);
    requestAnimationFrame(() => element.classList.add("is-collapsed"));
  });
}

/** Wraps content into a `.collapsible` so it can be expanded/collapsed by height. */
export function collapsible(content, extraClass = "") {
  return h("div", { class: ["collapsible", extraClass] }, h("div", {}, content));
}
