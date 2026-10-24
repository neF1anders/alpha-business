/**
 * Tiny observable store. `update` takes a function returning a partial state,
 * subscribers get (state, previousState) and decide themselves what to redraw.
 */
export function createStore(initialState) {
  let state = initialState;
  const listeners = new Set();

  return {
    get: () => state,
    update(producer) {
      const previous = state;
      state = { ...state, ...producer(state) };
      for (const listener of listeners) listener(state, previous);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** Subscribes to a derived value and fires only when it changes (by reference). */
export function watch(store, select, onChange) {
  let current = select(store.get());
  onChange(current, undefined);
  return store.subscribe((state) => {
    const next = select(state);
    if (next === current) return;
    const previous = current;
    current = next;
    onChange(next, previous);
  });
}
