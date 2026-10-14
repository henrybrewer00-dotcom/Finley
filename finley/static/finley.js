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
      const ws = $("#ws"), form = $("#find-form");
      if (!ws || !form) return;
      const prompt = $("#find-prompt"), sel = $("#find-campaign"), thread = $("#ws-thread"), rows = $("#find-rows");
      const offerField = $("#find-offer-field");
      const syncOffer = () => offerField && offerField.classList.toggle("hidden", sel.value !== "new");
      sel.addEventListener("change", syncOffer); syncOffer();
      let mode = "describe", busy = false;
      let cid = ws.dataset.campaign || "";
      let base = prompt.value.trim();
      const totals = { leads: rows.children.length, companies: 0, noEmail: 0, cost: 0 };
      const placeholders = {
        describe: "Owner-operated plumbing companies in Austin, TX with 5 to 50 employees that offer 24/7 emergency service",
        like: "acme.com, or a customer you'd love ten more of",
      };
      document.querySelectorAll(".mode-tab").forEach((t) => t.addEventListener("click", () => {
        mode = t.dataset.mode;
        document.querySelectorAll(".mode-tab").forEach((x) => x.classList.toggle("active", x === t));
        prompt.placeholder = placeholders[mode]; prompt.focus();
      }));
      const scroll = () => { thread.scrollTop = thread.scrollHeight; };
      const say = (cls, html) => { thread.insertAdjacentHTML("beforeend", `<div class="msg ${cls}">${html}</div>`); scroll(); return thread.lastElementChild; };
      const logo = ($(".logo") || { outerHTML: "" }).outerHTML;
      const agent = (html) => say("msg-agent", `<div class="agent-ic">${logo}</div><div class="agent-body">${html}</div>`);
      const money = (v) => "$" + (v || 0).toFixed(2);
      const paint = () => {
        $("#st-leads").textContent = totals.leads;
        $("#st-companies").textContent = totals.companies || "–";
        $("#st-noemail").textContent = totals.companies ? totals.noEmail : "–";
        $("#st-cost").textContent = totals.companies ? money(totals.cost) : "–";
        $("#sheet-empty").classList.toggle("hidden", totals.leads > 0);
      };

      async function run(description, shown) {
        if (busy || !description) return;
        busy = true;
        ws.dataset.state = "sheet";
        const demo = $("#find-demo").checked;
        const limit = parseInt($("#find-limit").value, 10) || 25;
        say("msg-user", esc(shown || description));
        const msg = agent(`<div class="row" style="--gap:8px"><span class="spinner"></span><span class="stage">Planning searches…</span></div><ul class="steps-done"></ul>`);
        const stageEl = $(".stage", msg), done = $(".steps-done", msg);
        const progress = $("#find-progress"), bar = $("#find-bar");
        progress.classList.remove("hidden");
        const stages = ["Planning searches…", "Searching the web for matching companies…", "Reading company websites…", "Finding the right person at each company…", "Checking email addresses…"];
        let si = 0;
        const setStage = (t) => { stageEl.textContent = t; $("#find-stage").textContent = t; };
        const rotate = setInterval(() => { si = Math.min(si + 1, stages.length - 1); setStage(stages[si]); }, 7000);
        let added = 0, companies = 0;
        try {
          if (!cid || sel.value === "new") {
            const c = await postJSON("/api/campaigns", { icp: description, offer: ($("#find-offer") || {}).value || "" });
            cid = String(c.id);
            sel.add(new Option(c.name || description.slice(0, 44), cid, true, true)); syncOffer();
            $("#ws-title").textContent = $("#sheet-title").textContent = c.name || "New campaign";
            $("#sheet-tags").innerHTML = `<span class="tag">${esc(description.length > 70 ? description.slice(0, 69) + "…" : description)}</span>${demo ? '<span class="tag tag-demo">Demo</span>' : ""}`;
            history.replaceState(null, "", `/app/find?campaign=${cid}`);
          }
          $("#find-next").href = `/app/campaigns/${cid}?tab=overview#run`;
          for (let round = 0; round < 6 && added < limit; round++) {
            const stop = creep(bar, Math.max(3, (100 * added) / limit), 92, 45);
            let r;
            try {
              r = await postJSON(`/api/campaigns/${cid}/find-leads`, { description, limit: limit - added, exclude_domains: ($("#find-exclude") || {}).value || "", dry_run: demo, api_key: ($("#find-key") || {}).value || "" });
            } finally { stop(); }
            const c = r.counts || {};
            companies += c.companies_found || 0;
            totals.companies += c.companies_found || 0; totals.noEmail += c.no_email || 0; totals.cost += r.cost_usd || 0;
            (r.leads || []).forEach((l) => rows.insertAdjacentHTML("beforeend", Finley.leadRow(l)));
            added += c.leads_added || 0; totals.leads += c.leads_added || 0;
            done.insertAdjacentHTML("beforeend", `<li>Checked ${c.companies_found || 0} companies, added ${c.leads_added || 0} with a public email</li>`);
            $("#find-count").textContent = `${added} of ${limit}`;
            bar.style.width = Math.max(5, (100 * added) / limit) + "%";
            paint(); scroll();
            if (!c.leads_added || demo) break;
          }
          clearInterval(rotate);
          $(".spinner", msg).remove();
          stageEl.innerHTML = added
            ? `Found <b>${added}</b> lead${added === 1 ? "" : "s"} from ${companies} companies.${demo ? " Demo searches return a few sample companies; add your API key to search the real web." : ""} Next, I can research each one and write their email.`
            : "No new leads with a public email this time. Try a broader description or a different area.";
          if (added) $(".agent-body", msg).insertAdjacentHTML("beforeend", `<div class="row" style="--gap:8px;margin-top:10px"><a class="btn btn-sm btn-primary" href="/app/campaigns/${cid}?tab=overview#run">${$("#find-next").innerHTML}</a><button type="button" class="btn btn-sm" data-more>Find more</button></div>`);
          $("#find-next").classList.toggle("hidden", totals.leads === 0);
        } catch (e) {
          clearInterval(rotate);
          const sp = $(".spinner", msg); sp && sp.remove();
          stageEl.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
        } finally {
          progress.classList.add("hidden");
          busy = false; scroll();
        }
      }

      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const text = prompt.value.trim();
        if (!text) return;
        base = mode === "like" ? `Companies similar to ${text}: same industry, size and kind of customer` : text;
        run(base, text);
      });
      $("#ws-follow").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const box = $("#ws-follow-input"), text = box.value.trim();
        if (!text) return;
        box.value = "";
        const more = /^\s*(\d+\s*)?more\b/i.test(text) && text.split(/\s+/).length <= 3;
        run(more ? base : `${base}. Also: ${text}`, text);
      });
      $("#ws-follow-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); $("#ws-follow").requestSubmit(); } });
      prompt.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); form.requestSubmit(); } });
      $("#sheet-more").addEventListener("click", () => run(base, "Find more like these"));
      thread.addEventListener("click", (ev) => { if (ev.target.closest("[data-more]")) run(base, "Find more like these"); });
      paint();
    },

    leadRow(l) {
      const site = (l.website || "").replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "");
      const name = [l.first_name, l.last_name].filter(Boolean).join(" ");
      const band = l.score == null ? "" : l.score >= 75 ? "A" : l.score >= 50 ? "B" : l.score >= 25 ? "C" : "D";
      const fit = band ? `<span class="fit fit-${band}"><i></i>${Math.round(l.score)}</span>` : '<span class="fit fit-new"><i></i>New</span>';
      const co = l.company || site || "?";
      return `<tr class="row-new">
        <td class="rn"></td>
        <td>${fit}</td>
        <td><div class="co"><span class="co-ic">${esc(co.charAt(0).toUpperCase())}</span><div><div class="cell-main">${esc(co)}</div>${site ? `<a class="cell-sub" href="${esc(l.website)}" target="_blank" rel="noopener">${esc(site)}</a>` : ""}</div></div></td>
        <td>${name ? `<div>${esc(name)}</div><div class="cell-sub">${esc(l.title || "")}</div>` : '<span class="cell-sub">Shared inbox</span>'}</td>
        <td class="nowrap"><div>${esc(l.email)}</div><div class="cell-sub">${l.email_source === "email_finder" ? "Email finder" : "On their website"}</div></td>
        <td class="why">${esc(l.why_fit || "")}</td></tr>`;
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
