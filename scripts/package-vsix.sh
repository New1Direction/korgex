#!/usr/bin/env bash
# Package the KorgKode VS Code extension into a .vsix for one-click install.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$HERE/../korgkode-vscode"
VSIX="$EXT_DIR/korgkode-sidecar.vsix"

echo "  Packaging KorgKode VS Code extension..."

cd "$EXT_DIR"

# Ensure deps and compile
if [ ! -d node_modules ]; then
    echo "  Installing Node dependencies..."
    npm install --silent
fi

echo "  Compiling TypeScript..."
npx tsc -p ./ --outDir out

# Check if vsce is available
if command -v npx &> /dev/null && npx --yes vsce --version &> /dev/null 2>&1; then
    npx vsce package --allow-missing-repository -o "$VSIX" 2>&1
elif command -v vsce &> /dev/null; then
    vsce package --allow-missing-repository -o "$VSIX" 2>&1
else
    echo "  vsce not found. Creating minimal .vsix manually..."

    # Create a minimal .vsix (it's a ZIP with .vsix extension)
    TMPDIR=$(mktemp -d)
    mkdir -p "$TMPDIR/extension"

    cp package.json "$TMPDIR/extension/"
    mkdir -p "$TMPDIR/extension/out"
    cp out/extension.js "$TMPDIR/extension/out/"
    cp out/extension.js.map "$TMPDIR/extension/out/" 2>/dev/null || true

    # Create extension.vsixmanifest
    cat > "$TMPDIR/extension/extension.vsixmanifest" <<- EOM
<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Id="korgkode-sidecar" Version="1.0.0" Publisher="KorgKode" />
    <DisplayName>KorgKode Autonomy</DisplayName>
    <Description>IDE sidecar for the KorgKode AI Swarm</Description>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.VSIXPackage" Path="extension.vsixmanifest" />
  </Assets>
</PackageManifest>
EOM

    # Add [Content_Types].xml
    cat > "$TMPDIR/[Content_Types].xml" <<- EOM
<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="vsixmanifest" ContentType="text/xml" />
  <Default Extension="js" ContentType="text/javascript" />
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="map" ContentType="application/json" />
</Types>
EOM

    # Zip it up
    cd "$TMPDIR"
    zip -r "$VSIX" . -q
    rm -rf "$TMPDIR"
fi

echo "  ✓ VSIX created: $VSIX"
echo "  Install with:  code --install-extension $VSIX"