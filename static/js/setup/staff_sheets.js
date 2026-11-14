/*
 * The sheets on Setup, Staff (setup/templates/setup/setup_staff.html):
 * pick a role or a location from a list, set a pay rate, set a new
 * password. Each takes SweetAlert2 as an argument (node tests it with a
 * fake) and resolves to the answer, or null when the person backs out or
 * picks what was already there. Titles are text, never read as HTML.
 */
const LOOK = (dark) => ({
  confirmButtonColor: "var(--accent-gold)",
  cancelButtonColor: "var(--text-muted)",
  background: dark ? "var(--panel-bg)" : "#ffffff",
  color: "var(--text-main)",
  customClass: { popup: "rounded-0", confirmButton: "rounded-0 btn btn-accent", cancelButton: "rounded-0 btn btn-luxury" },
  showCancelButton: true,
});

export async function pickOne(swal, { title, options, current, confirmText, dark = false }) {
  const { value } = await swal.fire({
    ...LOOK(dark), titleText: title, input: "select", inputOptions: options, inputValue: current, confirmButtonText: confirmText,
  });
  return value && String(value) !== String(current) ? value : null;
}

export function payRateProblem(amount) {
  return !amount || !(Number.parseFloat(amount) > 0) ? "Enter an amount greater than 0" : "";
}

export async function askPayRate(swal, { title, dark = false }) {
  const { value } = await swal.fire({
    ...LOOK(dark),
    titleText: title,
    html:
      '<select id="swal-pay-type" class="swal2-select pay-type" aria-label="Pay type">' +
      '<option value="monthly">Monthly Salary</option><option value="hourly">Hourly Rate</option></select>' +
      '<input id="swal-pay-amount" type="number" min="0.01" step="0.01" class="swal2-input pay-amount" placeholder="Amount (₹)" aria-label="Amount">',
    confirmButtonText: "Save Pay Rate",
    preConfirm: () => {
      const popup = swal.getPopup();
      const amount = popup.querySelector("#swal-pay-amount").value;
      const problem = payRateProblem(amount);
      if (problem) {
        swal.showValidationMessage(problem);
        return false;
      }
      return { pay_type: popup.querySelector("#swal-pay-type").value, amount };
    },
  });
  return value || null;
}

export function passwordProblem(value) {
  return !value || value.length < 8 ? "Password must be at least 8 characters." : null;
}

export async function askPassword(swal, { title, dark = false }) {
  const { value } = await swal.fire({
    ...LOOK(dark),
    titleText: title,
    input: "password",
    inputLabel: "New password (min 8 characters)",
    inputPlaceholder: "Enter a new password",
    inputAttributes: { autocomplete: "new-password" },
    confirmButtonText: "Reset Password",
    inputValidator: passwordProblem,
  });
  return value || null;
}
