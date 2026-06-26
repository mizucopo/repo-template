import { describe, expect, it } from "vitest";

import { formatExtensionTitle } from "../../src/lib/extension-title.js";

describe("formatExtensionTitle", () => {
  it("combines the extension name and version", () => {
    // Arrange
    const extensionName = "Test Extension";
    const version = "0.1.0";

    // Act
    const title = formatExtensionTitle(extensionName, version);

    // Assert
    expect(title).toBe("Test Extension 0.1.0");
  });
});
