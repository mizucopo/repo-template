const installedListeners = new Set();

export function resetChromeMock() {
  installedListeners.clear();
  globalThis.chrome = {
    runtime: {
      onInstalled: {
        addListener(listener) {
          installedListeners.add(listener);
        },
      },
    },
  };
}

export function triggerInstalled(details) {
  for (const listener of installedListeners) {
    listener(details);
  }
}

resetChromeMock();
