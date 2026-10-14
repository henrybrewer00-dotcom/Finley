/* Finley front end. Small, dependency-free helpers bound per page. */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, opts || {}));
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw new Error(data.error || data.detail || ("Request failed (" + res.status + ")"));
    return data;
  }
  const postJSON = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
  const postForm = (url, fields) => {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
    return api(url, { method: "POST", body: fd });
  };

  // A progress bar that creeps toward a ceiling while we wait on a long request.
  function creep(bar, from, to, seconds) {
    let pct = from;
    const step = (to - pct) / (seconds * 4);
    const t = setInterval(() => { pct = Math.min(to, pct + step * Math.max(0.15, (to - pct) / (to - from || 1))); bar.style.width = pct + "%"; }, 250);
    return () => clearInterval(t);
  }

  const Finley = {
    bindExamples() {
      const box = $("#find-prompt");
      document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => { box.value = b.dataset.example; box.focus(); }));
    },

    bindFind() {
      const form = $("#find-form");
      if (!form) return;
      const sel = $("#find-campaign"), offerField = $("#find-offer-field");
      const syncOffer = () => offerField.classList.toggle("hidden", sel.value !== "new");
      sel.addEventListener("change", syncOffer); syncOffer();

      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const btn = $("#find-btn"), progress = $("#find-progress"), stage = $("#find-stage"), bar = $("#find-bar");
        const description = $("#find-prompt").value.trim();
        const limit = parseInt($("#find-limit").value, 10) || 25;
        const demo = $("#find-demo").checked;
        if (!description) return;
        btn.disabled = true;
        progress.classList.remove("hidden");
        $("#find-spinner").classList.remove("hidden");
        $("#find-explainer") && $("#find-explainer").classList.add("hidden");
        const stages = ["Planning searches…", "Searching the web for matching companies…", "Reading company websites…", "Finding the right person at each company…", "Checking email addresses…"];
        let si = 0; stage.textContent = stages[0];
        const rotate = setInterval(() => { si = Math.min(si + 1, stages.length - 1); stage.textContent = stages[si]; }, 7000);
        const rows = $("#find-rows"), results = $("#find-results");
        let added = 0, companies = 0, noEmail = 0, cost = 0, cid = sel.value;
        try {
          if (cid === "new") {
            const c = await postJSON("/api/campaigns", { icp: description, offer: ($("#find-offer") || {}).value || "" });
            cid = String(c.id);
            const opt = new Option(description.slice(0, 60), cid, true, true);
            sel.add(opt); syncOffer();
          }
          for (let round = 0; round < 6 && added < limit; round++) {
            const stop = creep(bar, Math.max(3, (100 * added) / limit), Math.min(95, (100 * (added + (limit - added) * 0.8)) / limit), 45);
            let r;
            try {
              r = await postJSON(`/api/campaigns/${cid}/find-leads`, { description, limit: limit - added, exclude_domains: $("#find-exclude").value, dry_run: demo, api_key: ($("#find-key") || {}).value || "" });
            } finally { stop(); }
            const c = r.counts || {};
            companies += c.companies_found || 0; noEmail += c.no_email || 0; cost += r.cost_usd || 0;
            (r.leads || []).forEach((l) => rows.insertAdjacentHTML("beforeend", Finley.leadRow(l)));
            added += c.leads_added || 0;
            $("#find-count").textContent = `${added} of ${limit}`;
            bar.style.width = Math.max(5, (100 * added) / limit) + "%";
            if (!c.leads_added || demo) break;
          }
          clearInterval(rotate);
          $("#find-spinner").classList.add("hidden");
          stage.textContent = added ? `Done. Added ${added} lead${added === 1 ? "" : "s"}.${demo ? " Demo searches return a few sample companies; add your API key to search the real web." : ""}` : "No new leads with a public email this time. Try a broader description or a different area.";
          bar.style.width = "100%";
          results.classList.remove("hidden");
          $("#find-results-title").textContent = `${added} lead${added === 1 ? "" : "s"} added`;
          $("#find-results-sub").textContent = `${companies} companies checked · ${noEmail} had no public email · $${cost.toFixed(2)} AI spend`;
          $("#find-next").href = `/app/campaigns/${cid}?tab=overview#run`;
          $("#find-next").classList.toggle("hidden", !added);
        } catch (e) {
          clearInterval(rotate);
          $("#find-spinner").classList.add("hidden");
          stage.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
        } finally {
          btn.disabled = false;
        }
      });
    },

    leadRow(l) {
      const site = (l.website || "").replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "");
      const name = [l.first_name, l.last_name].filter(Boolean).join(" ");
      const src = l.email_source === "email_finder" ? '<span class="badge badge-blue">Email finder</span>' : '<span class="badge badge-green">On their website</span>';
      return `<tr>
        <td><div class="cell-main">${esc(l.company)}</div><div class="cell-sub">${site ? `<a href="${esc(l.website)}" target="_blank" rel="noopener">${esc(site)}</a>` : ""}</div></td>
        <td>${name ? `<div>${esc(name)}</div><div class="cell-sub">${esc(l.title || "")}</div>` : '<span class="faint small">Shared inbox</span>'}</td>
        <td class="nowrap"><div>${esc(l.email)}</div><div style="margin-top:3px">${src}</div></td>
        <td class="small" style="max-width:380px">${esc(l.why_fit || "")}</td></tr>`;
    },

    bindRun() {
      const btn = $("#run-btn");
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const cid = btn.dataset.campaign;
        const limit = parseInt($("#run-limit").value, 10) || 25;
        const fields = { min_score: $("#run-min").value || "50", exclude_domains: $("#run-exclude").value || "", demo: $("#run-demo").checked ? "true" : "", api_key: ($("#run-key") || {}).value || "" };
        const box = $("#run-progress"), log = $("#run-log"), bar = $("#run-bar"), status = $("#run-status"), count = $("#run-count");
        btn.disabled = true; box.classList.remove("hidden"); log.innerHTML = "";
        const tally = { drafted: 0, filtered: 0, below_threshold: 0, errors: 0 };
        try {
          const q = await api(`/api/campaigns/${cid}/queue?limit=${limit}`);
          const leads = q.leads || [];
          let done = 0;
          for (const lead of leads) {
            const who = esc([lead.first_name, lead.last_name].filter(Boolean).join(" ") || lead.company || "Lead");
            status.innerHTML = `<span class="spinner"></span>Researching and writing for ${who}${lead.company ? " at " + esc(lead.company) : ""}…`;
            count.textContent = `${done} of ${leads.length}`;
            let r;
            try { r = await postForm(`/api/leads/${lead.id}/process`, fields); } catch (e) { r = { result: "errors", error: e.message }; }
            tally[r.result] = (tally[r.result] || 0) + 1;
            const line = { drafted: `<span class="ok">✓ Email written for ${who}</span> <span class="faint">score ${Math.round(r.score || 0)}</span>`,
              below_threshold: `<span class="faint">– ${who}: score ${Math.round(r.score || 0)}, below your bar</span>`,
              filtered: `<span class="faint">– ${who}: not a fit</span>`,
              skipped: `<span class="faint">– ${who}: already done</span>`,
              errors: `<span class="err">✕ ${who}: ${esc(r.error || "failed")}</span>` }[r.result] || "";
            log.insertAdjacentHTML("afterbegin", `<div>${line}</div>`);
            done++;
            bar.style.width = (100 * done) / leads.length + "%";
            if (r.result === "errors" && /API key/.test(r.error || "")) break;
          }
          count.textContent = `${done} of ${leads.length}`;
          bar.style.width = "100%";
          status.innerHTML = `Done: ${tally.drafted} written, ${tally.below_threshold} below your bar, ${tally.filtered} not a fit${tally.errors ? `, ${tally.errors} failed` : ""}.`;
          if (tally.drafted) {
            status.insertAdjacentHTML("beforeend", ` <a class="btn btn-sm btn-primary" href="/app/review">Review emails</a>`);
          }
        } catch (e) {
          status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
        } finally {
          btn.disabled = false;
        }
      });
    },
  };
  window.Finley = Finley;
})();
