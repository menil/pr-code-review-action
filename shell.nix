{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    just
    python3
    ruff
    python3Packages.pytest
    mypy
  ];
}
