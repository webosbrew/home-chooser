// Home Chooser E2E fixture launcher (web). The id and the variant are filled in by build.sh.
// Records every launch (webOSLaunch) and relaunch (webOSRelaunch) with its parameters:
//   1. console.log "HCFIXTURE ..." (reaches /var/log/messages only where WAM logs JS console
//      messages; not established),
//   2. webOSSystem.PmLogString when the page has it (best effort),
//   3. localStorage "hcfixture.log" / "hcfixture.count", readable through the web inspector.
// The system's own record of the launch (sam logs the app id and parameters) is the
// fallback the runner relies on.
(function () {
  var ID = '@APPID@';
  var VARIANT = '@VARIANT@';
  var count = 0;

  function bump() {
    try { count = parseInt(localStorage.getItem('hcfixture.count') || '0', 10) + 1; } catch (e) { count += 1; }
    try { localStorage.setItem('hcfixture.count', String(count)); } catch (e2) {}
  }
  function paint(note) {
    document.body.className = VARIANT;
    document.getElementById('id').textContent = ID;
    document.getElementById('variant').textContent = 'WEB ' + VARIANT.toUpperCase();
    document.getElementById('count').textContent = 'launch #' + count + (note ? ' (' + note + ')' : '');
  }
  function record(kind, params) {
    var line = 'HCFIXTURE ' + kind + ' id=' + ID + ' variant=' + VARIANT + ' count=' + count +
      ' t=' + Date.now() + ' params=' + JSON.stringify(params === undefined ? null : params);
    console.log(line);
    try {
      if (window.webOSSystem && webOSSystem.PmLogString) { webOSSystem.PmLogString(6, 'HCFIXTURE', '{}', line); }
    } catch (e) {}
    try {
      var log = JSON.parse(localStorage.getItem('hcfixture.log') || '[]');
      log.push(line);
      localStorage.setItem('hcfixture.log', JSON.stringify(log.slice(-50)));
    } catch (e2) {}
  }
  function launchParams() {
    try { return JSON.parse(window.PalmSystem && PalmSystem.launchParams || 'null'); } catch (e) { return null; }
  }

  bump();
  paint('');
  record('launch', launchParams());
  document.addEventListener('webOSRelaunch', function (ev) {
    bump();
    paint('relaunch');
    record('relaunch', launchParams());
  });
  document.addEventListener('keydown', function (ev) {
    // Back (461) and Escape close the page like any app.
    if (ev.keyCode === 461 || ev.keyCode === 27) { window.close(); }
  });
})();
