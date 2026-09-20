# codex-router integration

Required upstream: https://github.com/duolahypercho/codex-router (reviewed baseline `5e1b49e6`). These are local source hooks, not a supported upstream plugin API. Apply only when the anchors and variables below exist; inspect newer versions rather than patching blindly.

## 1. Configure the router service

Use the existing router service manager to supply the variables in `../examples/config.env.example` to its process. Preserve existing service settings. Keep credentials on the remote host; do not put them in the router config or this repository. Ensure the service can use your SSH alias with BatchMode authentication and an already-verified host key.

Generate the adapter file URL from the package directory (handles spaces correctly):

```bash
node --input-type=module -e 'import {pathToFileURL} from "node:url"; console.log(pathToFileURL(process.cwd()+"/src/hermes-picker-adapter.mjs").href)'
```

Set that value as `HERMES_PICKER_ADAPTER_MODULE`. Configuration examples are not loaded automatically.

To enable selected-skill delivery before Hermes generation, set `HERMES_PICKER_SKILL_INJECTION=true` and `HERMES_PICKER_SKILL_PROFILE` to a reviewed host-local profile, then restart the router service. Set the flag to `false` and restart to disable it. Timeout, cancellation, uncertainty, invalid output, database failure, or oversized bodies preserve the original user request and fall back to native discovery; aggregate mapping telemetry records IDs and counts, never task or body content.

## 2. Request handler hook

Back up the router files outside the public repository. In `src/router.mjs`, immediately after `requestedModel` is assigned from the decoded payload, and before registered-model lookup, insert:

```js
    if (requestedModel === "hermes-vps/agent" || requestedModel === "hermes-vps-agent") {
      try {
        const moduleUrl = process.env.HERMES_PICKER_ADAPTER_MODULE;
        if (!moduleUrl) throw new Error("Configure HERMES_PICKER_ADAPTER_MODULE as an absolute file URL");
        const { HermesPickerAdapter } = await import(moduleUrl);
        globalThis.__hermesPickerAdapter ||= new HermesPickerAdapter();
        if (!globalThis.__hermesPickerUiStarted) {
          const { startPickerUi } = await import(new URL("./hermes-picker-ui.mjs", moduleUrl).href);
          await startPickerUi({ adapter: globalThis.__hermesPickerAdapter });
          globalThis.__hermesPickerUiStarted = true;
        }
        const hermesResult = await globalThis.__hermesPickerAdapter.handleResponses({
          request,
          response,
          payload,
          signal: controller.signal,
        });
        finalStatus = hermesResult?.status || response.statusCode;
        activityStatus = finalStatus;
        usageRecorded = true;
      } catch (error) {
        if (!response.headersSent) {
          writeJson(response, 503, {
            error: {
              type: "hermes_picker_unavailable",
              message: String(error?.message || error),
            },
          });
        }
        finalStatus = response.headersSent ? (response.statusCode || 503) : 503;
        activityStatus = finalStatus;
        usageRecorded = true;
      }
      return;
    }
```

This block depends on the existing handler's `request`, `response`, `payload`, `controller`, `finalStatus`, `activityStatus`, `usageRecorded` and `writeJson` bindings. If they changed, adapt and test before deployment. The early return is essential: this route must never fall through into ordinary inference routing.

## 3. Catalog hook

In `src/catalog.mjs` inside `publishCatalog`, after construction of `merged` and before catalog snapshots/publication, insert:

```js
  // Explicit local agent adapter: an opt-in picker entry, not a model provider.
  // Removing its private entry file removes it on the next publication.
  const hermesEntryPath = path.join(path.dirname(CONFIG_PATH), "hermes-picker", "catalog-entry.json");
  if (existsSync(hermesEntryPath)) {
    const entry = JSON.parse(readFileSync(hermesEntryPath, "utf8"));
    if (entry.slug !== "hermes-vps/agent" || !merged.length) {
      throw new Error("Invalid local Hermes picker entry");
    }
    merged.push({ ...merged[0], ...entry });
  }
```

This uses the existing imports `path`, `CONFIG_PATH`, `existsSync`, and `readFileSync`. In the same function, change its visibility policy assignment to:

```js
const routerManaged = slug !== "hermes-vps/agent" && (loginFree || !nativeBaseSlugs.has(slug));
```

Copy `examples/catalog-entry.json` from this package to `hermes-picker/catalog-entry.json` beside the router's `CONFIG_PATH` (normally `$CODEX_HOME/config.toml`, so `~/.codex/hermes-picker/catalog-entry.json`). Create the directory privately and set the file mode to `0600`. The entry inherits unspecified fields from the first merged model; this integration requires a nonempty existing catalog and must be reviewed when the catalog schema changes.

## 4. Validate and activate

From the package: `npm test`.

From the router checkout:

```bash
node --check src/router.mjs
node --check src/catalog.mjs
npm run check
node --input-type=module -e 'import {publishCatalog} from "./src/catalog.mjs"; publishCatalog({refreshNative:false,output:false});'
bin/control service restart
```

Use the service manager appropriate to your existing installation if `bin/control` is unavailable. Verify the restarted service actually receives the configuration. Reopen Codex if its catalog is cached. Run a single isolated text canary with `codex exec --model hermes-vps/agent`, then resume that task and verify remembered context. Check the persisted mapping references the same remote session. An unsupported model error usually means the catalog or hook was not loaded; do not route it to a different model as fallback.

For the chooser, open the private URL from the local `ui.json` after the first Hermes request. Verify the selected task and session before attaching. Keep live transcripts, session IDs, capability URLs, file contents and verification logs out of the repository.

## Removal and upgrades

Remove the private catalog-entry file and republish to hide the entry. Remove only the hook blocks and restore the original visibility expression to uninstall, then restart the service. Compare local diffs before a router update; upstream replacement can remove the hooks. This package does not modify or restart remote Hermes.
