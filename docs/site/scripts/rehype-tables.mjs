export default function rehypeTables() {
  return function walk(node) {
    if (node.properties?.className?.includes("table-scroll")) return;
    node.children?.forEach((child, index) => {
      walk(child);
      if (child.type === "element" && child.tagName === "table") {
        node.children[index] = {
          type: "element",
          tagName: "div",
          properties: {
            className: ["table-scroll"],
            tabIndex: 0,
            role: "region",
            ariaLabel: "Scrollable table",
          },
          children: [child],
        };
      }
    });
  };
}
