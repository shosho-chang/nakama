import { describe, it, expect } from "vitest";
import {
  NoVaultError,
  FSAPermissionRevokedError,
  ExtractionFailedError,
  VaultWriteFailedError,
} from "../../src/shared/errors.js";

describe("NoVaultError", () => {
  it("is an Error instance", () => {
    expect(new NoVaultError()).toBeInstanceOf(Error);
  });

  it("has name NoVaultError", () => {
    expect(new NoVaultError().name).toBe("NoVaultError");
  });

  it("has descriptive message", () => {
    expect(new NoVaultError().message).toBe("No vault selected");
  });
});

describe("FSAPermissionRevokedError", () => {
  it("is an Error instance", () => {
    expect(new FSAPermissionRevokedError()).toBeInstanceOf(Error);
  });

  it("has name FSAPermissionRevokedError", () => {
    expect(new FSAPermissionRevokedError().name).toBe("FSAPermissionRevokedError");
  });

  it("has descriptive message", () => {
    expect(new FSAPermissionRevokedError().message).toBe("Vault permission revoked");
  });
});

describe("ExtractionFailedError", () => {
  it("is an Error instance", () => {
    expect(new ExtractionFailedError("timeout")).toBeInstanceOf(Error);
  });

  it("has name ExtractionFailedError", () => {
    expect(new ExtractionFailedError("x").name).toBe("ExtractionFailedError");
  });

  it("includes reason in message", () => {
    expect(new ExtractionFailedError("page empty").message).toBe(
      "Extraction failed: page empty",
    );
  });
});

describe("VaultWriteFailedError", () => {
  it("is an Error instance", () => {
    expect(new VaultWriteFailedError("disk full")).toBeInstanceOf(Error);
  });

  it("has name VaultWriteFailedError", () => {
    expect(new VaultWriteFailedError("x").name).toBe("VaultWriteFailedError");
  });

  it("includes reason in message", () => {
    expect(new VaultWriteFailedError("read-only").message).toBe(
      "Vault write failed: read-only",
    );
  });
});
