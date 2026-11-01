# Launcher API description

[`launcher-v1.openrpc.json`](launcher-v1.openrpc.json) is a machine-readable description of the
launcher API, version 1, in [OpenRPC](https://spec.open-rpc.org/) 1.3.2. Version 1 is frozen (see
[Stability](../docs/launcher-api.md#stability), tag `api-v1`), and this file describes it: the eight
methods of the freeze and the methods and fields added within version 1 since, their parameters and
replies as JSON Schema, every `errorCode` each method can send, and examples. [`docs/launcher-api.md`](../docs/launcher-api.md) is the prose contract and wins
where the two differ; a change to either must keep them in step.

## What is in it

* **Methods.** `getInfo`, `listApps`, `listRunning`, `getForegroundApp`, `getAppInfo`, `getAppIcon`,
  `closeApp` and `uninstallApp`, and, added within v1, `listLaunchPoints`, `listInputs` and
  `getIcons`, with named parameters (`paramStructure: by-name`). Facts the guide
  states about a method are `x-` fields on it: `x-needsRoot`, `x-needsPermission`,
  `x-needsSelectedHome`, `x-subscribes`, `x-forJailedLauncher`, `x-ignoredParams`, `x-notes` and so
  on.
* **What is not a method** is in top-level `x-` fields: the luna service name and URI (`x-luna`),
  the permission (`x-permission`), the `appCategory: ["home"]` and `requiredPermissions` appinfo
  declarations (`x-appinfo`), the `request` launch parameter (`x-requestLaunch`), the error table
  (`x-errors`) and the order of the caller checks (`x-replyModel`). `x-luna.subscriptionModel` says
  how the lists' `subscribe: true` works; `x-subscribes` marks the methods that take it.
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

## Client and mock

[`client/`](client) is generated from the description by
[`tools/gen-client.py`](tools/gen-client.py) (`python3 api/tools/gen-client.py`; standard library
only). Do not edit the files by hand.

* [`homechooser.js`](client/homechooser.js): one file, no dependencies, ES5 plus `Promise` (the web
  runtime of webOS 5, Chromium 68, and later). A `<script>` include defines the global
  `HomeChooser`; `require` returns the same object. One promise-returning function per method
  (`HomeChooser.getAppIcon({id})`), using the bridge call of the guide. A promise resolves with
  the reply when `returnValue` is true and otherwise rejects with a `HomeChooserError`: `source`
  is `homechooser` (a reply with `apiVersion`; `errorCode` and `errorText` as the service sent
  them), `platform` (the luna bus answered: no `apiVersion`, often a numeric `errorCode`) or
  `transport` (no bridge, no reply within 70 seconds, or not JSON). `requestHome(appId?)` sends the
  request launch, `ownAppId()` reads the app's id from the web runtime, and `lunaCall(uri, payload)`
  makes any other luna call, such as `applicationManager/launch`. `ERROR_CODES` lists each method's
  codes. `subscribe(method, params, onReply, onError)` opens a subscription of `listApps`,
  `listLaunchPoints` or `listInputs`: `onReply` gets every successful reply (the whole list, again
  after each change), `onError` the `HomeChooserError` that ended it, and the returned object's
  `cancel()` ends it.
* [`homechooser.d.ts`](client/homechooser.d.ts): its types.
* [`homechooser-mock.js`](client/homechooser-mock.js): the same API on fake data, for opening a
  launcher in a desktop browser.

To use the mock, include it after the client:

```html
<script src="homechooser.js"></script>
<script src="homechooser-mock.js"></script>
```

It switches itself on only where the page has no webOS service bridge (`WebOSServiceBridge` or
`PalmServiceBridge`), which is a desktop browser; on a TV it does nothing, so leaving the include in
a package changes nothing. It never takes over silently in the other direction: without the include,
a missing bridge is a `transport` error. In the browser it replaces the client's transport, so the
real client code runs on fake replies: every method answers from `HomeChooser.mock` (the spec's
examples: the `listApps`, `listLaunchPoints`, `listInputs`, `listRunning`, `getForegroundApp`,
`getAppIcon` and `getIcons` success examples, the caller `org.example.launcher`), applies the caller
checks in the order of `x-replyModel`, and validates `id`, `size` and `items` as the param schemas
do. Subscriptions answer at once, again on `HomeChooser.mock.notify(method)`, and refuse the 17th
subscriber of a list; `?hcmock-changes=<ms>` flips the last input's `connected` every `<ms>` and
notifies the `listInputs` subscribers. `applicationManager/launch` is logged and
succeeds. `?hcmock=<state>` in the page URL picks the `getInfo` state: `selected-home` (default),
`before-elevation`, `not-permitted`, `not-home`, or `absent` (every call gets a platform error, as
when Home Chooser is not installed). `HomeChooser.mock` can be changed in the console.

[`examples/web-launcher`](../examples/web-launcher/README.md) carries copies of both `.js` files;
the generator writes those too.

## How it is checked

The checks are CTest tests in [`service/test`](../service/test/CMakeLists.txt), run by
[`launcher-spec.py`](../service/test/launcher-spec.py) and the scripts in [`tools/`](tools) with
nothing but the Python standard library (and `node` for the client's behaviour). All but the
contract test also run with `-DQEMU_TESTS=OFF`:

```sh
cmake -S service/test -B build/service-host && cmake --build build/service-host
ctest --test-dir build/service-host --output-on-failure -R launcher
```

* `test-launcher-spec` checks the file against the OpenRPC meta-schema, every schema for keywords
  the checker does not implement, every example and contract case result against its method's
  result schema and its params against the param schemas, every fixture against its format, and
  that every `errorCode` of every method is produced by at least one example or case.
* `test-launcher-doc` checks [`docs/launcher-api.md`](../docs/launcher-api.md) against the file:
  one heading per method under "Methods", each method's `Errors:` paragraph naming exactly the
  method's `errorCode` values, the methods table agreeing with `x-needsRoot`, `x-needsPermission`
  and `x-needsSelectedHome`, and the "Error handling" table listing exactly `x-errors`.
* `test-launcher-client` fails if a file in `client/` or its copy in `examples/web-launcher` is not
  what `gen-client.py` makes from the file now. `test-launcher-client-js`, run when CMake finds
  `node`, loads the client and the mock in node with a fake bridge
  ([`tools/client-test.js`](tools/client-test.js), checked by [`tools/test-client.py`](tools/test-client.py)):
  replies, rejections and their `source`, the request launch payload, subscriptions (several
  replies, a refusal or a transport failure ending one, one bridge cancel) and every mock reply
  validated against its method's result schema.
* `test-launcher-contract` runs every example, and the further cases in
  [`launcher-contract-cases.json`](../service/test/launcher-contract-cases.json), through the real
  handlers of `service/src/service/launcher.c` and `catalog.c` (with the real `subscribe.c`,
  `reply.c`, `luna.c`, `apps.c`, `json.c`, `fs.c` and `policy.c`, cross built and run under qemu-arm with the firmware's pbnjson
  and GLib). It checks that each reply validates against the result schema and equals the expected
  one, and that the handler made exactly the expected luna-send calls, as
  `com.webos.applicationManager`, with the expected payloads and time limits
  (`x-outgoingCalls`). A first reply with `subscribed: true` must leave the message in the fake
  hub's subscription catalog, and every other reply must not; `subscribers` in a fixture fills the
  catalog first, for the cap.
* `test-launcher-subscribe` runs the subscriptions themselves: the real upstream `luna-send -i`
  children of `luna-child.c`, with `service/test/fake-luna-send.py` as luna-send, on the fake hub. It
  checks the first reply, one shared upstream for two subscribers, a change sent to every subscriber
  as a fresh full read, a burst of changes read once, an upstream that dies and comes back after the
  backoff, the subscriber cap, and that cancelling the last subscriber kills and reaps the upstream.

The driver, [`test-launcher-contract.c`](../service/test/test-launcher-contract.c), replaces the
luna hub with [`fake-ls2.c`](../service/test/fake-ls2.c) and five functions at link time
(`--wrap`): `run_argv` (the luna-send child), `geteuid`, `read_choice` (the stored Home choice),
`open64` (to make `/dev/urandom` unavailable) and `hc_fs_prefix` (the fixed icon directories of
launch points and inputs lie under the case's scratch directory). The upstream subscription children
are stubbed there. The production code has no test hooks.

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
