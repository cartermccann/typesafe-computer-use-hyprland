{
  description = "typesafe-computer-use Hyprland/NixOS fork — system tools for the Linux adapter";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [
              uv
              python312
              grim
              tesseract
              ydotool
              wtype
              at-spi2-core
              # hyprctl comes from a Hyprland session; include CLI helpers if needed:
              hyprland
            ];
            shellHook = ''
              export CLICKER_PLATFORM=hyprland
              export YDOTOOL_SOCKET="''${YDOTOOL_SOCKET:-/run/ydotoold/socket}"
              echo "CLICKER_PLATFORM=hyprland — uv sync --extra hyprland && uv run clicker \"…\""
            '';
          };
        });
    };
}
