import { cp, mkdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const sourceRoot = pathToFileURL(`${resolve("src")}/`);
const distRoot = pathToFileURL(`${resolve("dist")}/`);
const assets = ["manifest.json", "popup.html", "popup.css"];

await mkdir(distRoot, { recursive: true });

await Promise.all(assets.map((asset) => cp(new URL(asset, sourceRoot), new URL(asset, distRoot))));
