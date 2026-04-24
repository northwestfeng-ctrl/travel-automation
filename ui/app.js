const navItems = document.querySelectorAll(".nav-item");
const panels = document.querySelectorAll("[data-view]");
const approvalStatus = document.querySelector("#approvalStatus");
const syncButton = document.querySelector("#syncButton");
const decisionButtons = document.querySelectorAll("[data-decision]");

function showPanel(panelName) {
  navItems.forEach((item) => {
    item.classList.toggle("active", item.dataset.panel === panelName);
  });

  panels.forEach((panel) => {
    const views = panel.dataset.view.split(" ");
    panel.classList.toggle("is-hidden", !views.includes(panelName));
  });
}

navItems.forEach((item) => {
  item.addEventListener("click", () => showPanel(item.dataset.panel));
});

decisionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const isApproved = button.dataset.decision === "approved";
    approvalStatus.textContent = isApproved ? "本地预览：已批准" : "本地预览：已拒绝";
    approvalStatus.className = `status-pill ${isApproved ? "" : "warning"}`;
  });
});

syncButton.addEventListener("click", () => {
  syncButton.textContent = "已同步 17:06";
  approvalStatus.textContent = "审批待确认";
  approvalStatus.className = "status-pill warning";
});
