document.querySelectorAll("[data-toggle-password]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.querySelector(button.dataset.togglePassword);
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Show" : "Hide";
  });
});
const password = document.querySelector("#reg-password");
const strength = document.querySelector("#strength");
if (password && strength) password.addEventListener("input", () => {
  const score = [password.value.length >= 8, /[A-Z]/.test(password.value), /[a-z]/.test(password.value), /\d/.test(password.value)].filter(Boolean).length;
  strength.textContent = score < 3 ? "Use 8+ characters with upper/lowercase letters and a number." : "Strong password";
  strength.className = score < 3 ? "weak" : "good";
});
document.querySelectorAll("input[name=role]").forEach((input) => input.addEventListener("change", () => {
  document.querySelectorAll("[data-role]").forEach((field) => {
    const selected = document.querySelector("input[name=role]:checked")?.value;
    field.hidden = !field.dataset.role.split(" ").includes(selected);
  });
}));

document.querySelector("[data-sidebar-toggle]")?.addEventListener("click", () => {
  document.querySelector("[data-dashboard]")?.classList.toggle("sidebar-open");
});

document.querySelector("[data-notification-toggle]")?.addEventListener("click", () => {
  document.querySelector("[data-notification-panel]")?.classList.toggle("is-visible");
});

document.querySelector("[data-profile-toggle]")?.addEventListener("click", () => {
  document.querySelector("[data-profile-menu]")?.classList.toggle("is-visible");
});

document.querySelector("[data-price-search]")?.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  document.querySelectorAll("[data-price-row]").forEach((row) => {
    row.hidden = query && !row.textContent.toLowerCase().includes(query);
  });
});
