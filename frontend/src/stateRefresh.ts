export function createStateRefresher<T>(load: () => Promise<T>, apply: (state: T) => void) {
  let pending: Promise<void> | undefined;
  return async (fresh = false): Promise<void> => {
    if (fresh && pending) await pending.catch(() => undefined);
    if (!pending) {
      const request = load().then(apply).finally(() => {
        if (pending === request) pending = undefined;
      });
      pending = request;
    }
    await pending;
  };
}
