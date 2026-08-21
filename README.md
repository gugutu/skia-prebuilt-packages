# Skia Prebuilt Packages

Builds immutable, private-consumer Skia SDK packages for Apple, Android, and
Windows. The package is a complete static archive plus the public headers and
link metadata required to consume exactly that archive.

Each package contains:

- `include/skia`: the enabled Skia and module header closure;
- `include/harfbuzz`: the public C headers for Skia's bundled HarfBuzz instance;
- `lib`: one complete static archive;
- `abi.json`: verified bundled native ABI declarations and symbols;
- build metadata, GN arguments, checksums, and third-party licenses.

Consumers must not link another HarfBuzz implementation. The bundled headers
and symbols intentionally make the HarfBuzz instance owned by Skia reusable by
the application's text engine without a handwritten duplicate C ABI.

Tags and release assets are immutable. This repository remains a private
consumer contract; it is not a general-purpose public Skia distribution.
