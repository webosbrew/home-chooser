# Launcher API description

[`launcher-v1.openrpc.json`](launcher-v1.openrpc.json) is a machine-readable description of the
launcher API, version 1, in [OpenRPC](https://spec.open-rpc.org/) 1.3.2. Version 1 is frozen (see
[Stability](../docs/launcher-api.md#stability), tag `api-v1`), and this file describes it as frozen:
the eight methods, their parameters and replies as JSON Schema, every `errorCode` each method can
send, and examples. [`docs/launcher-api.md`](../docs/launcher-api.md) is the prose contract and wins
where the two differ; a change to either must keep them in step.

## What is in it

* **Methods.** `getInfo`, `listApps`, `listRunning`, `getForegroundApp`, `getAppInfo`, `getAppIcon`,
  `closeApp` and `uninstallApp`, with named parameters (`paramStructure: by-name`). Facts the guide
  states about a method are `x-` fields on it: `x-needsRoot`, `x-needsPermission`,
  `x-needsSelectedHome`, `x-subscribes`, `x-forJailedLauncher`, `x-ignoredParams`, `x-notes` and so
  on.
* **What is not a method** is in top-level `x-` fields: the luna service name and URI (`x-luna`),
  the permission (`x-permission`), the `appCategory: ["home"]` and `requiredPermissions` appinfo
  declarations (`x-appinfo`), the `request` launch parameter (`x-requestLaunch`), the error table
  (`x-errors`) and the order of the caller checks (`x-replyModel`).
* **Errors are part of the result.** A reply is a luna reply, not a JSON-RPC response: success and
  failure are both one JSON object, told apart by `returnValue`. Each method's result schema is a
  `oneOf` of the success object and the failure object (`returnValue: false`, `apiVersion`,
  `errorCode`, `errorText`), with the method's `errorCode` values as an `enum`. OpenRPC's own
  `errors` were not used: an OpenRPC error object has an integer `code` and describes a JSON-RPC
  error member, which this service never sends, so it could not describe the JSON on the wire.
  Replies from the luna bus itself (unknown service or method, `Denied method call`) do not come
  from Home Chooser and are described in `x-replyModel.platformErrors`, not in the schemas.
* **Objects are open.** Success objects do not set `additionalProperties: false`, because fields
  may be added within v1. The `errorCode` enums are closed: they are what v1 sends today. A client
  still treats an unknown `errorCode` as a failure, as the guide says.
* **Examples with fixtures.** Each example pairing carries `x-fixture`, the platform state the
  contract test needs: the caller's application id, root state, the stored Home choice, files on
  disk and every call the service makes to the application manager or notification service, in
  order, with its scripted answer. `x-match` lists values that depend on the build (the package
  `version`) as regular expressions. The format is in `x-fixtureFormat`. `errorText` values in the
  examples are what the service sends today; they are not part of the contract.

## How it is checked

Both checks are CTest tests in [`service/test`](../service/test/CMakeLists.txt), run by
[`launcher-spec.py`](../service/test/launcher-spec.py) with nothing but the Python standard library:

```sh
cmake -S service/test -B build/service-host && cmake --build build/service-host
ctest --test-dir build/service-host --output-on-failure -R launcher
```

* `test-launcher-spec` checks the file against the OpenRPC meta-schema, every schema for keywords
  the checker does not implement, every example and contract case result against its method's
  result schema and its params against the param schemas, every fixture against its format, and
  that every `errorCode` of every method is produced by at least one example or case. It needs only
  python3, so it also runs with `-DQEMU_TESTS=OFF`.
* `test-launcher-contract` runs every example, and the further cases in
  [`launcher-contract-cases.json`](../service/test/launcher-contract-cases.json), through the real
  handlers of `service/src/service/launcher.c` (with the real `reply.c`, `luna.c`, `apps.c`,
  `json.c`, `fs.c` and `policy.c`, cross built and run under qemu-arm with the firmware's pbnjson
  and GLib). It checks that each reply validates against the result schema and equals the expected
  one, and that the handler made exactly the expected luna-send calls, as
  `com.webos.applicationManager`, with the expected payloads and time limits
  (`x-outgoingCalls`).

The driver, [`test-launcher-contract.c`](../service/test/test-launcher-contract.c), replaces the
luna hub with [`fake-ls2.c`](../service/test/fake-ls2.c) and four functions at link time
(`--wrap`): `run_argv` (the luna-send child), `geteuid`, `read_choice` (the stored Home choice) and
`open64` (to make `/dev/urandom` unavailable). The production code has no test hooks.

A case that follows the guide where the code does otherwise carries `x-knownDisagreement` (in a
case file, `knownDisagreement`) with the reason and both locations. The contract test reports it as
an expected failure, and fails if it passes, so that a stale marker is removed.

The JSON Schema checker, [`tools/launcher_schema.py`](tools/launcher_schema.py), is a draft-07
subset shared by `launcher-spec.py`, the client generator and the `--api-check` reply validation of
the end-to-end runner. It refuses any keyword it does not implement, so a schema cannot pass on an
unchecked keyword; `format` is not checked.

## Vendored meta-schemas

[`vendor/`](vendor) holds the two meta-schemas the lint needs, so it runs offline. Both are
Apache-2.0, unchanged in content, pretty-printed:

* `openrpc-meta-schema-1.14.9.json`: the OpenRPC document meta-schema (`https://meta.open-rpc.org/`,
  OpenRPC 1.0.0-rc0 to 1.3.2), the `openrpcDocument` object of the npm package
  `@open-rpc/meta-schema` 1.14.9 (https://github.com/open-rpc/meta-schema).
* `json-schema-tools-meta-schema-1.8.3.json`: the JSON Schema meta-schema it references
  (`https://meta.json-schema.tools/`), `schema.json` of the npm package
  `@json-schema-tools/meta-schema` 1.8.3 (https://github.com/json-schema-tools/meta-schema).
