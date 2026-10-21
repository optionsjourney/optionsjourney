// Preview order: draws the previewed order's legs (priced at the mids, in the page as JSON)
// with the journal's risk graph (risk_graph.js's _mountRiskGraphGroup). Module scripts run after
// the classic risk_graph.js and the inline module that sets window.__modeler.

const graph = JSON.parse(document.getElementById('preview-graph').textContent);
window._mountRiskGraphGroup([graph], { theo: false, title: view => view.items[0].label });
