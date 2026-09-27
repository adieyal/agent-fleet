// Synthetic demo observations, preclassified by Execution.
export const demoEvents = {
  "bash": [
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "pytest tests/invoices/test_upload.py -q", "activity_class": "test"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "npx vitest run src/v2/suppliers", "activity_class": "test"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git diff --stat", "activity_class": "review"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "ruff check app/suppliers", "activity_class": "test"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "make migrate", "activity_class": "build"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "npm run lint -- --fix", "activity_class": "test"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git log --oneline -5", "activity_class": "review"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git commit -m \"Port supplier filters to the V2 route\"", "activity_class": "ship"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git push -u origin HEAD", "activity_class": "ship"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "npm install", "activity_class": "build"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "npm run build", "activity_class": "build"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "docker compose up -d db", "activity_class": "build"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "sleep 30", "activity_class": "wait"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "curl -s https://api.github.com/repos/example/demo-store/pulls", "activity_class": "web"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "ssh node-b fleet ls", "activity_class": "web"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "psql -c \"select count(*) from invoices\"", "activity_class": "search"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "ls -la fleet/web", "activity_class": "search"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "mypy app/invoices", "activity_class": "test"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git add -A", "activity_class": "type"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "python scripts/export_suppliers.py --dry-run", "activity_class": "type"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "cat package.json", "activity_class": "type"},
    {"kind": "tool", "tool": "bash", "name": "Bash", "summary": "git checkout -b fix/upload-poller", "activity_class": "type"}
  ],
  "edit": [
    {"kind": "tool", "tool": "edit", "name": "Edit", "summary": "frontend/src/v2/routes/suppliers.tsx", "activity_class": "edit"},
    {"kind": "tool", "tool": "edit", "name": "Edit", "summary": "app/invoices/parser/luc.py", "activity_class": "edit"},
    {"kind": "tool", "tool": "edit", "name": "Edit", "summary": "fleet/web/index.html", "activity_class": "edit"},
    {"kind": "tool", "tool": "edit", "name": "Write", "summary": "docs/guides/onboarding.md", "activity_class": "doc"},
    {"kind": "tool", "tool": "edit", "name": "Edit", "summary": "app/suppliers/adapters.py", "activity_class": "edit"},
    {"kind": "tool", "tool": "edit", "name": "Edit", "summary": "tests/test_fleetd_parsers.py", "activity_class": "edit"}
  ],
  "read": [
    {"kind": "tool", "tool": "read", "name": "Read", "summary": "app/suppliers/views.py", "activity_class": "read"},
    {"kind": "tool", "tool": "read", "name": "Read", "summary": "docs/adr/0002-module-refactor.md", "activity_class": "read"},
    {"kind": "tool", "tool": "read", "name": "Read", "summary": "frontend/src/v2/router.tsx", "activity_class": "read"},
    {"kind": "tool", "tool": "read", "name": "Read", "summary": "fleet/remote/fleetd.py", "activity_class": "read"},
    {"kind": "tool", "tool": "read", "name": "Read", "summary": "invoices/sample-0412.json", "activity_class": "read"}
  ],
  "search": [
    {"kind": "tool", "tool": "search", "name": "Grep", "summary": "SupplierRow", "activity_class": "search"},
    {"kind": "tool", "tool": "search", "name": "Glob", "summary": "**/*.spec.ts", "activity_class": "search"},
    {"kind": "tool", "tool": "search", "name": "Grep", "summary": "luc_tolerance", "activity_class": "search"},
    {"kind": "tool", "tool": "search", "name": "Glob", "summary": "docs/**/*.png", "activity_class": "search"}
  ],
  "web": [
    {"kind": "tool", "tool": "web", "name": "WebFetch", "summary": "https://tanstack.com/router/latest/docs/guide/data-loading", "activity_class": "web"},
    {"kind": "tool", "tool": "web", "name": "WebFetch", "summary": "https://docs.python.org/3/library/decimal.html", "activity_class": "web"},
    {"kind": "tool", "tool": "web", "name": "WebFetch", "summary": "https://playwright.dev/docs/screenshots", "activity_class": "web"}
  ],
  "think": [
    {"kind": "tool", "tool": "think", "name": "", "summary": "Weighing whether the tolerance should be relative to the line total…", "activity_class": "think"},
    {"kind": "tool", "tool": "think", "name": "", "summary": "The flake only happens when the poller fires twice…", "activity_class": "think"},
    {"kind": "tool", "tool": "think", "name": "", "summary": "Two ways to split the loader; the second keeps parity…", "activity_class": "think"}
  ],
  "plan": [
    {"kind": "tool", "tool": "plan", "name": "TodoWrite", "summary": "{\"todos\": […]}", "activity_class": "plan"}
  ],
  "delegate": [
    {"kind": "tool", "tool": "delegate", "name": "Agent", "summary": "Find every caller of get_active_restaurants", "activity_class": "delegate"},
    {"kind": "tool", "tool": "delegate", "name": "Agent", "summary": "List routes still on the legacy table", "activity_class": "delegate"}
  ]
};
