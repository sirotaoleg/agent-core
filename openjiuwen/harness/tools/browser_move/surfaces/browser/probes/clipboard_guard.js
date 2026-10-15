// Runs in every document before the page's own scripts: the managed Chrome shares the host clipboard,
// so a read is refused however it is spelled, not only when a script's text names it.
(() => {
  const denied =
    "Blocked: the clipboard belongs to the host machine, not to this task, so pasting or reading it is not allowed. " +
    "Read the value from the page instead (its text or an input's value) and type it.";
  const reject = function () {
    return Promise.reject(new Error(denied));
  };
  const frozen = (value) => ({ value: value, configurable: false, writable: false });
  const clipboards = [];
  if (typeof Clipboard !== "undefined" && Clipboard.prototype) {
    clipboards.push(Clipboard.prototype);
  }
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    clipboards.push(navigator.clipboard);
  }
  for (const clipboard of clipboards) {
    for (const name of ["read", "readText"]) {
      try {
        Object.defineProperty(clipboard, name, frozen(reject));
      } catch (error) {
        // already frozen by an earlier install on this document
      }
    }
  }
  const documents = [];
  if (typeof Document !== "undefined" && Document.prototype) {
    documents.push(Document.prototype);
  }
  if (typeof document !== "undefined") {
    documents.push(document);
  }
  for (const target of documents) {
    const execCommand = target.execCommand;
    if (typeof execCommand !== "function") {
      continue;
    }
    const guarded = function (command, ...rest) {
      if (String(command).toLowerCase() === "paste") {
        return false;
      }
      return execCommand.apply(this, [command, ...rest]);
    };
    try {
      Object.defineProperty(target, "execCommand", frozen(guarded));
    } catch (error) {
      // already frozen by an earlier install on this document
    }
  }
})();
