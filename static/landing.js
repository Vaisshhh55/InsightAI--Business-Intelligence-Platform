const authState = {
  mode: "login",
  loading: false,
};

const elements = {
  tabLogin: document.getElementById("tabLogin"),
  tabRegister: document.getElementById("tabRegister"),
  fullNameGroup: document.getElementById("nameGroup"),
  fullNameInput: document.getElementById("fullNameInput"),
  emailInput: document.getElementById("emailInput"),
  passwordInput: document.getElementById("passwordInput"),
  confirmGroup: document.getElementById("confirmGroup"),
  confirmPasswordInput: document.getElementById("confirmPasswordInput"),
  rememberCheckbox: document.getElementById("rememberCheckbox"),
  togglePassword: document.getElementById("togglePassword"),
  authMessage: document.getElementById("authMessage"),
  loginButton: document.getElementById("loginButton"),
  registerButton: document.getElementById("registerButton"),
  themeToggle: document.getElementById("themeToggle"),
  toast: document.getElementById("toast"),
};

const errors = {
  nameError: document.getElementById("nameError"),
  emailError: document.getElementById("emailError"),
  passwordError: document.getElementById("passwordError"),
  confirmError: document.getElementById("confirmError"),
};

function setMode(mode) {
  authState.mode = mode;
  const isRegister = mode === "register";
  elements.tabLogin.classList.toggle("active", !isRegister);
  elements.tabRegister.classList.toggle("active", isRegister);
  elements.tabLogin.setAttribute("aria-selected", String(!isRegister));
  elements.tabRegister.setAttribute("aria-selected", String(isRegister));
  elements.fullNameGroup.classList.toggle("hidden", !isRegister);
  elements.confirmGroup.classList.toggle("hidden", !isRegister);
  elements.authMessage.textContent = isRegister
    ? "Create your enterprise InsightAI account."
    : "Enter your credentials to continue.";
  elements.loginButton.textContent = "Login";
  elements.registerButton.textContent = "Register";
  if (isRegister) {
    elements.loginButton.classList.remove("primary-btn");
    elements.loginButton.classList.add("secondary-btn");
    elements.registerButton.classList.remove("secondary-btn");
    elements.registerButton.classList.add("primary-btn");
  } else {
    elements.loginButton.classList.add("primary-btn");
    elements.loginButton.classList.remove("secondary-btn");
    elements.registerButton.classList.remove("primary-btn");
    elements.registerButton.classList.add("secondary-btn");
  }
  clearErrors();
}

function clearErrors() {
  Object.values(errors).forEach((field) => {
    if (field) field.textContent = "";
  });
}

function showToast(message, type = "success") {
  if (!elements.toast) return;
  elements.toast.textContent = message;
  elements.toast.className = `toast show ${type}`;
  window.clearTimeout(window.toastTimer);
  window.toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("show");
  }, 3800);
}

function setLoading(isLoading) {
  authState.loading = isLoading;
  const buttons = [elements.loginButton, elements.registerButton, elements.themeToggle, elements.togglePassword];
  buttons.forEach((btn) => {
    if (!btn) return;
    btn.disabled = isLoading;
    btn.style.opacity = isLoading ? "0.65" : "1";
  });
  if (isLoading) {
    elements.authMessage.textContent = authState.mode === "register" ? "Creating account, please wait..." : "Signing in, please wait...";
  }
}

function validateForm() {
  clearErrors();
  const email = elements.emailInput.value.trim();
  const password = elements.passwordInput.value;
  const confirmPassword = elements.confirmPasswordInput.value;
  const fullName = elements.fullNameInput.value.trim();
  let valid = true;
  if (authState.mode === "register") {
    if (!fullName || fullName.length < 3) {
      errors.nameError.textContent = "Enter your full name.";
      valid = false;
    }
  }
  if (!email) {
    errors.emailError.textContent = "Email is required.";
    valid = false;
  } else if (!/^\S+@\S+\.\S+$/.test(email)) {
    errors.emailError.textContent = "Enter a valid email address.";
    valid = false;
  }
  if (!password) {
    errors.passwordError.textContent = "Password is required.";
    valid = false;
  } else if (password.length < 8) {
    errors.passwordError.textContent = "Password must be at least 8 characters.";
    valid = false;
  }
  if (authState.mode === "register") {
    if (!confirmPassword) {
      errors.confirmError.textContent = "Confirm your password.";
      valid = false;
    } else if (confirmPassword !== password) {
      errors.confirmError.textContent = "Passwords must match.";
      valid = false;
    }
  }
  return valid;
}

async function sendAuth(endpoint, payload) {
  const response = await fetch(`/auth/${endpoint}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response;
}

async function handleAuth(action) {
  if (!validateForm()) {
    showToast("Please fix the highlighted input errors.", "error");
    return;
  }
  setLoading(true);
  const email = elements.emailInput.value.trim();
  const password = elements.passwordInput.value;
  const payload = { email, password };
  if (authState.mode === "register") {
    payload.full_name = elements.fullNameInput.value.trim();
  }
  try {
    const response = await sendAuth(action, payload);
    const result = await response.json();
    if (!response.ok) {
      showToast(result.error || "Authentication failed.", "error");
      elements.authMessage.textContent = result.error || "Check your credentials and try again.";
      setLoading(false);
      return;
    }
    showToast(result.message || "Success! Redirecting...", "success");
    try {
      await fetch("/auth/csrf", { credentials: "same-origin" });
    } catch (ex) {
      console.warn("CSRF fetch failed", ex);
    }
    window.location.href = "/workspace";
  } catch (ex) {
    showToast("Unable to reach authentication service.", "error");
    elements.authMessage.textContent = "Please check your connection and try again.";
    setLoading(false);
  }
}

function bindEvents() {
  elements.togglePassword.addEventListener("click", () => {
    const isPassword = elements.passwordInput.type === "password";
    elements.passwordInput.type = isPassword ? "text" : "password";
    elements.togglePassword.textContent = isPassword ? "Hide" : "Show";
  });

  elements.tabLogin.addEventListener("click", () => setMode("login"));
  elements.tabRegister.addEventListener("click", () => setMode("register"));

  elements.loginButton.addEventListener("click", () => {
    if (authState.mode === "login") {
      handleAuth("login");
    } else {
      setMode("login");
    }
  });
  elements.registerButton.addEventListener("click", () => {
    if (authState.mode === "register") {
      handleAuth("register");
    } else {
      setMode("register");
    }
  });

  document.getElementById("authForm").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (authState.mode === "login") {
        handleAuth("login");
      } else {
        handleAuth("register");
      }
    }
  });

  elements.themeToggle.addEventListener("click", () => {
    const currentTheme = document.body.classList.contains("theme-dark") ? "dark" : "light";
    const nextTheme = currentTheme === "dark" ? "light" : "dark";
    document.body.classList.toggle("theme-dark", nextTheme === "dark");
    document.body.classList.toggle("theme-light", nextTheme === "light");
    elements.themeToggle.textContent = nextTheme === "dark" ? "Light" : "Dark";
  });

  document.getElementById("forgotLink").addEventListener("click", (event) => {
    event.preventDefault();
    showToast("Please contact your administrator to reset your password.", "success");
  });
}

window.addEventListener("DOMContentLoaded", () => {
  if (!elements.tabLogin || !elements.tabRegister) return;
  bindEvents();
  setMode("login");
});
