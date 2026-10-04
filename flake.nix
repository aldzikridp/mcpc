{
  description = "mcpc: a minimal Model Context Protocol client for hax";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
    in
    {
      packages = forAllSystems (
        pkgs:
        let
          mcpc = pkgs.python3.pkgs.buildPythonApplication {
            pname = "hax-mcp-bridge";
            version = "0.1.0";
            pyproject = true;
            src = self;

            build-system = [ pkgs.python3.pkgs.setuptools ];

            # The bridge is one stdlib file: no dependencies, no compiled parts.
            doCheck = true;
            checkPhase = ''
              runHook preCheck
              python scripts/mcp_bridge_selftest.py
              runHook postCheck
            '';
            pythonImportsCheck = [ "mcp_bridge" ];

            meta = {
              description = "Minimal Model Context Protocol client (list, call, read, stop)";
              mainProgram = "mcpc";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.all;
            };
          };
        in
        {
          inherit mcpc;
          default = mcpc;
        }
      );

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = pkgs.lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.mcpc;
          meta.description = "Run the mcpc MCP bridge";
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          # matches requires-python = ">=3.8"; nothing else is needed
          packages = [ pkgs.python3 ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
