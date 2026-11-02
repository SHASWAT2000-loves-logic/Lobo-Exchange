const listingForm = document.getElementById("listing-form");

if (listingForm) {
  const generateButton = document.getElementById("generate-ai");
  const status = document.getElementById("ai-status");
  const estimate = document.getElementById("ai-estimate");
  const readyBanner = document.getElementById("ai-ready-banner");
  const priceRange = document.getElementById("ai-price-range");
  const fallbackMessage = "AI suggestions are unavailable right now. You can continue creating the listing manually.";
  const reviewButton = document.getElementById("review-listing");
  const reviewPanel = document.getElementById("safety-review");
  const reviewStatus = document.getElementById("safety-status");
  const reviewSummary = document.getElementById("safety-summary");
  const reviewWarningsHeading = document.getElementById("safety-warnings-heading");
  const reviewWarnings = document.getElementById("safety-warnings");
  const reviewUnavailable = document.getElementById("safety-unavailable");
  const publishButton = document.getElementById("safety-publish");
  let reviewedSnapshot = null;
  let allowPublish = false;
  let reviewing = false;
  let generationRevision = 0;
  const suggestedValues = {};
  const suggestionBoxes = {};

  function sameSuggestion(name, current, suggested) {
    if (["price", "estimated_price_low", "estimated_price_high"].includes(name)) {
      return Number.isFinite(Number(current)) && Number(current) === Number(suggested);
    }
    const normalize = (text) => text.trim().replace(/\s+/g, " ").replace(/[.!?]+$/, "").toLowerCase();
    return normalize(current) === normalize(suggested);
  }

  function showSuggestion(name, value, displayValue = value) {
    const field = listingForm.elements[name];
    let box = suggestionBoxes[name];
    if (!box) {
      box = document.createElement("div");
      box.className = "ai-suggestion small mt-2";
      box.id = `ai-suggestion-${name}`;
      const line = document.createElement("div");
      const label = document.createElement("span");
      label.className = "text-muted";
      label.textContent = name === "price" ? "✨ Suggested range: " : "✨ Suggested: ";
      const suggestedText = document.createElement("span");
      suggestedText.className = "ai-suggestion-value text-break";
      suggestedText.style.whiteSpace = "pre-wrap";
      line.append(label, suggestedText);
      const actions = document.createElement("div");
      actions.className = "d-flex align-items-center gap-2 mt-1";
      const useButton = document.createElement("button");
      useButton.type = "button";
      useButton.className = "btn btn-sm btn-outline-secondary";
      useButton.textContent = "Use";
      useButton.setAttribute("aria-label", `Use AI suggestion for ${field.labels[0].textContent.trim()}`);
      useButton.addEventListener("click", () => {
        field.value = suggestedValues[name];
        box.hidden = true;
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.focus();
      });
      const dismissButton = document.createElement("button");
      dismissButton.type = "button";
      dismissButton.className = "btn btn-sm btn-link text-secondary p-0";
      dismissButton.textContent = "Dismiss";
      dismissButton.setAttribute("aria-label", `Dismiss AI suggestion for ${field.labels[0].textContent.trim()}`);
      dismissButton.addEventListener("click", () => { box.hidden = true; });
      actions.append(useButton, dismissButton);
      box.append(line, actions);
      field.insertAdjacentElement("afterend", box);
      suggestionBoxes[name] = box;
    }
    suggestedValues[name] = value;
    box.querySelector(".ai-suggestion-value").textContent = displayValue;
    box.hidden = false;
  }

  function fillEmptyOrOffer(name, value, displayValue = value) {
    const field = listingForm.elements[name];
    if (field.value.trim() === "") {
      field.value = value;
      if (suggestionBoxes[name]) suggestionBoxes[name].hidden = true;
    } else if (sameSuggestion(name, field.value, value)) {
      if (suggestionBoxes[name]) suggestionBoxes[name].hidden = true;
    } else {
      showSuggestion(name, value, displayValue);
    }
  }

  function listingSnapshot() {
    const photo = listingForm.elements.image.files[0];
    return JSON.stringify([
      listingForm.elements.title.value,
      listingForm.elements.description.value,
      listingForm.elements.category.value,
      listingForm.elements.price.value,
      listingForm.elements.estimated_price_low.value,
      listingForm.elements.estimated_price_high.value,
      listingForm.elements.original_estimated_price_low.value,
      listingForm.elements.original_estimated_price_high.value,
      photo ? [photo.name, photo.size, photo.lastModified] : null,
    ]);
  }

  function clearReview() {
    reviewedSnapshot = null;
    reviewPanel.hidden = true;
    reviewStatus.textContent = "";
  }

  for (const name of ["title", "description", "category", "price", "image", "estimated_price_low", "estimated_price_high"]) {
    for (const eventName of ["input", "change"]) {
      listingForm.elements[name].addEventListener(eventName, () => {
        if (name === "image") generationRevision += 1;
        clearReview();
      });
    }
  }

  function showReview(result, snapshot) {
    if (listingSnapshot() !== snapshot) {
      reviewStatus.textContent = "The listing changed during review. Please review it again.";
      return;
    }
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const reasons = Array.isArray(result.reasons) ? result.reasons : [];
    reviewWarnings.replaceChildren();
    warnings.forEach((warning, index) => {
      const item = document.createElement("li");
      const heading = document.createElement("strong");
      heading.textContent = warning;
      item.appendChild(heading);
      if (reasons[index]) {
        const detail = document.createElement("div");
        detail.className = "small";
        detail.textContent = reasons[index];
        item.appendChild(detail);
      }
      reviewWarnings.appendChild(item);
    });
    reviewSummary.textContent = warnings.length
      ? "Please review these possible concerns before publishing."
      : result.ai_unavailable
        ? "The AI review could not finish. Check the details yourself before publishing."
        : "No major concerns were found. Review the details before publishing.";
    reviewWarningsHeading.hidden = warnings.length === 0;
    reviewWarnings.hidden = warnings.length === 0;
    reviewUnavailable.hidden = !result.ai_unavailable;
    publishButton.textContent = warnings.length || result.ai_unavailable ? "Publish anyway" : "Publish listing";
    reviewPanel.classList.remove("review-ok", "review-warning", "review-info");
    reviewPanel.classList.add(warnings.length ? "review-warning" : result.ai_unavailable ? "review-info" : "review-ok");
    publishButton.classList.toggle("btn-primary", !warnings.length && !result.ai_unavailable);
    publishButton.classList.toggle("btn-outline-secondary", Boolean(warnings.length || result.ai_unavailable));
    reviewedSnapshot = snapshot;
    reviewPanel.hidden = false;
    reviewStatus.textContent = "";
    reviewPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  generateButton.addEventListener("click", async () => {
    if (reviewing) return;
    clearReview();
    const image = document.getElementById("image").files[0];
    if (!image) {
      status.textContent = "Choose an item photo first, then generate suggestions.";
      status.className = "small mt-2 text-danger";
      return;
    }

    const data = new FormData();
    data.append("csrf_token", listingForm.elements.csrf_token.value);
    data.append("image", image);
    for (const name of ["title", "description", "category", "price"]) {
      data.append(name, listingForm.elements[name].value);
    }
    const thisGeneration = ++generationRevision;
    generateButton.disabled = true;
    status.textContent = "Generating suggestions...";
    status.className = "small mt-2 text-muted";

    try {
      const response = await fetch(listingForm.dataset.suggestUrl, {
        method: "POST",
        body: data,
      });
      const suggestions = await response.json();
      // Ignore an old image or a response that arrives after Review begins.
      if (thisGeneration !== generationRevision) {
        status.textContent = "The previous AI request was not applied. Generate again if needed.";
        status.className = "small mt-2 text-muted";
        return;
      }
      if (!response.ok) {
        status.textContent = suggestions.error || fallbackMessage;
        status.className = "small mt-2 text-danger";
        return;
      }

      fillEmptyOrOffer("title", suggestions.title);
      fillEmptyOrOffer("description", suggestions.description);
      fillEmptyOrOffer("category", suggestions.category);
      fillEmptyOrOffer("estimated_price_low", suggestions.estimated_price_low);
      fillEmptyOrOffer("estimated_price_high", suggestions.estimated_price_high);
      listingForm.elements.original_estimated_price_low.value = suggestions.estimated_price_low;
      listingForm.elements.original_estimated_price_high.value = suggestions.estimated_price_high;
      const rangeLabel = `$${suggestions.estimated_price_low}–$${suggestions.estimated_price_high}`;
      fillEmptyOrOffer("price", (
        (Number(suggestions.estimated_price_low) + Number(suggestions.estimated_price_high)) / 2
      ).toFixed(2), `${rangeLabel} (approximate AI estimate)`);
      estimate.hidden = false;
      priceRange.textContent = `Suggested range: ${rangeLabel} (approximate AI estimate).`;
      priceRange.hidden = false;
      readyBanner.hidden = false;
      status.textContent = "Empty fields were filled. Review the compact suggestions beside fields you already entered.";
      status.className = "small mt-2 text-success";
    } catch {
      if (thisGeneration === generationRevision) {
        status.textContent = fallbackMessage;
        status.className = "small mt-2 text-danger";
      }
    } finally {
      generateButton.disabled = false;
    }
  });

  listingForm.addEventListener("submit", async (event) => {
    if (allowPublish) return;
    event.preventDefault();
    if (reviewing || !listingForm.reportValidity()) return;
    generationRevision += 1;
    const snapshot = listingSnapshot();
    reviewing = true;
    reviewButton.disabled = true;
    reviewStatus.textContent = "Checking the listing before publishing...";
    reviewPanel.hidden = true;
    try {
      const response = await fetch(listingForm.dataset.analyzeUrl, {
        method: "POST",
        body: new FormData(listingForm),
      });
      const result = await response.json();
      if (!response.ok && (response.status === 400 || response.status === 413)) {
        reviewStatus.textContent = result.error || "Check the listing fields and photo, then try again.";
        return;
      }
      if (!response.ok) throw new Error("Safety review unavailable");
      showReview(result, snapshot);
    } catch {
      showReview({ warnings: [], reasons: [], ai_unavailable: true }, snapshot);
    } finally {
      reviewing = false;
      reviewButton.disabled = false;
    }
  });

  document.getElementById("safety-edit").addEventListener("click", () => {
    clearReview();
    listingForm.elements.title.focus();
  });

  publishButton.addEventListener("click", () => {
    if (reviewedSnapshot !== listingSnapshot()) {
      clearReview();
      reviewStatus.textContent = "The listing changed. Please review it again before publishing.";
      return;
    }
    if (!listingForm.reportValidity()) return;
    allowPublish = true;
    listingForm.requestSubmit();
  });
}
