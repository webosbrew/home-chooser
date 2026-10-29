Recorded on pi/w22h-ombre (webOS 9.2.1) on 2026-10-29 using
`beanjar screenshot` and local `beanjar elements --image PNG --json`.
Only the per-element local `file` paths were removed from the JSON.

- `reboot-alert.json`: Later is undimmed; Reboot is dimmed.
- `reboot-target.json`: after UP, Reboot is undimmed; Later is dimmed.
- `uninstall-alert.json`: Cancel is undimmed; Uninstall is dimmed.
- `alert-service.log`: matching service request/accepted reply lines from the sample run.

Both system alerts stack buttons vertically and render no title header. The Home
Chooser title in the restart frame belongs to the application beneath the alert;
it is absent from the uninstall frame. Ownership requires the exact message,
exact two buttons in the alert band and matching service evidence from this request.
Other app controls behind the alert remain visible outside that band.

Source captures are in `e2e/results/alertpress-samples-20261029/`:
`reboot-proof.png`, `92-sample-reboot-focus-proof.png`, `uninstall-proof.png`.

`uninstall-alert-5x.json` was extracted offline from
`e2e/results/matrix-20261029-152539/w20l-jhericurl/12-3a-uninstall-alert-confirm.png`
with `beanjar elements --image PNG --json`; local `file` fields were removed.
The prompt is above the horizontal button row in the same OCR group. Both
buttons are undimmed; only Cancel is highlighted. This differs from the 9.x
contrast convention above.
