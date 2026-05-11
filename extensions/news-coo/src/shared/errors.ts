export class NoVaultError extends Error {
  constructor() {
    super("No vault selected");
    this.name = "NoVaultError";
  }
}

export class FSAPermissionRevokedError extends Error {
  constructor() {
    super("Vault permission revoked");
    this.name = "FSAPermissionRevokedError";
  }
}

export class ExtractionFailedError extends Error {
  constructor(reason: string) {
    super(`Extraction failed: ${reason}`);
    this.name = "ExtractionFailedError";
  }
}

export class VaultWriteFailedError extends Error {
  constructor(reason: string) {
    super(`Vault write failed: ${reason}`);
    this.name = "VaultWriteFailedError";
  }
}
