const form = document.querySelector("#review-upload");
const submit = form.querySelector("button[type=submit]");
const progress = document.querySelector("#review-progress");

form.addEventListener("submit", (event) => {
  if (submit.disabled) {
    event.preventDefault();
    return;
  }
  submit.disabled = true;
  form.setAttribute("aria-busy", "true");
  progress.textContent = "正在核对资料，请等待复核结果。";
});

window.addEventListener("pageshow", () => {
  submit.disabled = false;
  form.removeAttribute("aria-busy");
  progress.textContent = "";
});
