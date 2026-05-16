const $ = (selector) => document.querySelector(selector);

const colors = [
  "#006b57",
  "#b24b32",
  "#2f6cb3",
  "#9a6b00",
  "#6f4aa6",
  "#008c9e",
  "#c14f8a",
  "#477a2a",
  "#7a4b27",
  "#4059ad",
  "#a83232",
  "#1d7874"
];

const state = {
  playerCount: 4,
  sharedCost: 1.05,
  routes: [],
  highlightPlayer: null
};

function directCost(index) {
  return 1 / (index + 1);
}

function routeEdges(playerIndex, route) {
  if (route === "shared") {
    return [
      { id: "shared", label: "S-H", cost: state.sharedCost },
      { id: `tail-${playerIndex}`, label: `H-T${playerIndex + 1}`, cost: 0 }
    ];
  }
  return [{ id: `direct-${playerIndex}`, label: `S-T${playerIndex + 1}`, cost: directCost(playerIndex) }];
}

function edgeUsage(routes = state.routes) {
  const usage = new Map();
  routes.forEach((route, playerIndex) => {
    routeEdges(playerIndex, route).forEach((edge) => {
      if (!usage.has(edge.id)) {
        usage.set(edge.id, { ...edge, users: [] });
      }
      usage.get(edge.id).users.push(playerIndex);
    });
  });
  return usage;
}

function playerCost(playerIndex, routes = state.routes) {
  const usage = edgeUsage(routes);
  return routeEdges(playerIndex, routes[playerIndex]).reduce((sum, edge) => {
    const users = usage.get(edge.id)?.users.length || 1;
    return sum + edge.cost / users;
  }, 0);
}

function socialCost(routes = state.routes) {
  return [...edgeUsage(routes).values()].reduce((sum, edge) => sum + edge.cost, 0);
}

function harmonicCost(routes = state.routes) {
  return [...edgeUsage(routes).values()].reduce((sum, edge) => {
    const harmonic = Array.from({ length: edge.users.length }, (_, i) => 1 / (i + 1)).reduce((a, b) => a + b, 0);
    return sum + edge.cost * harmonic;
  }, 0);
}

function alternativeRoute(route) {
  return route === "shared" ? "direct" : "shared";
}

function bestImprovement(routes = state.routes) {
  let best = null;
  routes.forEach((route, playerIndex) => {
    const before = playerCost(playerIndex, routes);
    const nextRoutes = [...routes];
    nextRoutes[playerIndex] = alternativeRoute(route);
    const after = playerCost(playerIndex, nextRoutes);
    const saving = before - after;
    if (saving > 0.0001 && (!best || saving > best.saving)) {
      best = { playerIndex, before, after, saving, route: nextRoutes[playerIndex], routes: nextRoutes };
    }
  });
  return best;
}

function isStable(routes = state.routes) {
  return !bestImprovement(routes);
}

function allRouteProfiles(count) {
  const profiles = [];
  const total = 2 ** count;
  for (let mask = 0; mask < total; mask += 1) {
    const profile = [];
    for (let index = 0; index < count; index += 1) {
      profile.push(mask & (1 << index) ? "shared" : "direct");
    }
    profiles.push(profile);
  }
  return profiles;
}

function optimalProfile() {
  return allRouteProfiles(state.playerCount).reduce((best, profile) => {
    const cost = socialCost(profile);
    return !best || cost < best.cost ? { cost, profile } : best;
  }, null);
}

function stableProfiles() {
  return allRouteProfiles(state.playerCount).filter((profile) => isStable(profile));
}

function formatCost(value) {
  return Number(value).toFixed(3).replace(/\.?0+$/, "");
}

function resetGame() {
  state.playerCount = Math.max(2, Math.min(12, Number($("#player-count").value || 4)));
  state.sharedCost = Math.max(0.1, Number($("#shared-cost").value || 1.05));
  state.routes = Array.from({ length: state.playerCount }, (_, index) => (index % 2 === 0 ? "shared" : "direct"));
  state.highlightPlayer = null;
  render();
}

function render() {
  renderBoard();
  renderScores();
  renderPlayers();
  renderLedger();
}

function renderScores() {
  const social = socialCost();
  const optimum = optimalProfile();
  const stable = isStable();
  $("#social-cost").textContent = formatCost(social);
  $("#optimal-cost").textContent = formatCost(optimum.cost);
  $("#price-ratio").textContent = optimum.cost ? (social / optimum.cost).toFixed(2) : "1.00";
  $("#stability").textContent = stable ? "Stable" : "Improving move";

  const best = bestImprovement();
  const stableCount = stableProfiles().length;
  $("#move-note").textContent = best
    ? `Player ${best.playerIndex + 1} can save ${formatCost(best.saving)} by switching to ${best.route}.`
    : `${stableCount} stable profile${stableCount === 1 ? "" : "s"} found for ${state.playerCount} players.`;
}

function renderPlayers() {
  const list = $("#player-list");
  list.innerHTML = "";
  state.routes.forEach((route, playerIndex) => {
    const card = document.createElement("article");
    card.className = "player-card";
    if (state.highlightPlayer === playerIndex) card.classList.add("best-move");

    const title = document.createElement("div");
    title.className = "player-title";
    const label = document.createElement("strong");
    label.innerHTML = `<span class="player-badge" style="background:${colors[playerIndex % colors.length]}"></span> Player ${playerIndex + 1}`;
    const cost = document.createElement("span");
    cost.className = "player-cost";
    cost.textContent = formatCost(playerCost(playerIndex));
    title.append(label, cost);

    const toggle = document.createElement("div");
    toggle.className = "route-toggle";
    ["direct", "shared"].forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option === "direct" ? `Direct ${formatCost(directCost(playerIndex))}` : "Shared";
      button.classList.toggle("active", route === option);
      button.addEventListener("click", () => {
        state.routes[playerIndex] = option;
        state.highlightPlayer = null;
        render();
      });
      toggle.appendChild(button);
    });

    const saving = document.createElement("p");
    saving.className = "saving";
    const nextRoutes = [...state.routes];
    nextRoutes[playerIndex] = alternativeRoute(route);
    const gain = playerCost(playerIndex) - playerCost(playerIndex, nextRoutes);
    saving.textContent = gain > 0.0001 ? `Can save ${formatCost(gain)} by switching.` : "No cheaper switch.";

    card.append(title, toggle, saving);
    list.appendChild(card);
  });
}

function renderLedger() {
  const body = $("#edge-ledger");
  body.innerHTML = "";
  [...edgeUsage().values()]
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach((edge) => {
      const row = document.createElement("tr");
      const users = edge.users.map((index) => `P${index + 1}`).join(", ");
      row.innerHTML = `
        <td>${edge.label}</td>
        <td>${formatCost(edge.cost)}</td>
        <td>${users}</td>
        <td>${formatCost(edge.cost / edge.users.length)}</td>
      `;
      body.appendChild(row);
    });
}

function terminalY(index, count) {
  if (count === 1) return 220;
  return 60 + (index * 320) / (count - 1);
}

function renderBoard() {
  const svg = $("#network-board");
  const width = 900;
  const height = 440;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";

  const source = { x: 80, y: 220 };
  const hub = { x: 430, y: 220 };
  const terminalX = 800;
  const usage = edgeUsage();

  drawEdge(svg, source, hub, state.sharedCost, usage.get("shared")?.users || [], "shared", "M 80 220 C 190 130, 315 130, 430 220");
  drawNode(svg, source.x, source.y, 32, "S", false);
  drawNode(svg, hub.x, hub.y, 36, "H", true);

  state.routes.forEach((route, index) => {
    const terminal = { x: terminalX, y: terminalY(index, state.playerCount) };
    const directPath = `M ${source.x} ${source.y} C 260 ${20 + index * 18}, 560 ${terminal.y - 90}, ${terminal.x} ${terminal.y}`;
    const tailPath = `M ${hub.x} ${hub.y} C 540 ${hub.y}, 640 ${terminal.y}, ${terminal.x} ${terminal.y}`;
    drawEdge(svg, source, terminal, directCost(index), usage.get(`direct-${index}`)?.users || [], `direct-${index}`, directPath);
    drawEdge(svg, hub, terminal, 0, usage.get(`tail-${index}`)?.users || [], `tail-${index}`, tailPath);
    drawNode(svg, terminal.x, terminal.y, 24, `T${index + 1}`, false, colors[index % colors.length]);
  });
}

function drawEdge(svg, start, end, cost, users, id, pathData) {
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  path.setAttribute("class", `edge ${users.length ? "used" : ""}`);
  path.setAttribute("stroke-width", users.length ? String(3 + users.length) : "2.5");
  svg.appendChild(path);

  if (users.length) {
    const mid = midpointFromPath(pathData);
    users.slice(0, 10).forEach((playerIndex, offset) => {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("class", "player-dot");
      dot.setAttribute("cx", String(mid.x + (offset % 5) * 13 - 26));
      dot.setAttribute("cy", String(mid.y + Math.floor(offset / 5) * 13 - 6));
      dot.setAttribute("r", "7");
      dot.setAttribute("fill", colors[playerIndex % colors.length]);
      svg.appendChild(dot);
    });
  }

  const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
  const labelPoint = midpointFromPath(pathData);
  label.setAttribute("class", "edge-label");
  label.setAttribute("x", String(labelPoint.x));
  label.setAttribute("y", String(labelPoint.y - 18));
  label.setAttribute("text-anchor", "middle");
  label.textContent = id === "shared" ? `cost ${formatCost(cost)} / users` : `cost ${formatCost(cost)}`;
  svg.appendChild(label);
}

function midpointFromPath(pathData) {
  const numbers = pathData.match(/-?\d+(\.\d+)?/g).map(Number);
  return {
    x: (numbers[0] + numbers[numbers.length - 2]) / 2,
    y: (numbers[1] + numbers[numbers.length - 1]) / 2
  };
}

function drawNode(svg, x, y, radius, label, hub = false, accent = "") {
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  circle.setAttribute("class", `node ${hub ? "hub-node" : "terminal-node"}`);
  circle.setAttribute("cx", String(x));
  circle.setAttribute("cy", String(y));
  circle.setAttribute("r", String(radius));
  if (accent) circle.setAttribute("stroke", accent);
  svg.appendChild(circle);

  const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
  text.setAttribute("class", "node-label");
  text.setAttribute("x", String(x));
  text.setAttribute("y", String(y + 1));
  text.textContent = label;
  svg.appendChild(text);
}

$("#apply-game").addEventListener("click", resetGame);
$("#best-response").addEventListener("click", () => {
  const best = bestImprovement();
  if (!best) {
    state.highlightPlayer = null;
    render();
    return;
  }
  state.routes = best.routes;
  state.highlightPlayer = best.playerIndex;
  render();
});
$("#run-stable").addEventListener("click", () => {
  let guard = 40;
  let best = bestImprovement();
  while (best && guard > 0) {
    state.routes = best.routes;
    state.highlightPlayer = best.playerIndex;
    best = bestImprovement();
    guard -= 1;
  }
  render();
});
$("#set-optimal").addEventListener("click", () => {
  const optimum = optimalProfile();
  state.routes = optimum.profile;
  state.highlightPlayer = null;
  render();
});
$("#player-count").addEventListener("change", resetGame);
$("#shared-cost").addEventListener("change", resetGame);

resetGame();
