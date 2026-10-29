/** Tracks draft ownership without querying component internals. */
export class UnsavedChanges {
  private owners = new Set<string>();
  set(owner: string, dirty: boolean) {
    if (dirty) this.owners.add(owner);
    else this.owners.delete(owner);
  }
  get pending() {
    return this.owners.size > 0;
  }
}
export const unsavedChanges = new UnsavedChanges();
