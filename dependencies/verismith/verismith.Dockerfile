FROM nixos/nix

RUN git clone https://github.com/ymherklotz/verismith.git /app/verismith && \
    cd /app/verismith && \
    git checkout b8178487eebf126de8d8e060b1e976675ef9f510 && \
    nix-shell -p gnused --run "sed -i 's/s1 + s2/s1 + s2 + 1/' ./src/Verismith/Verilog/AST.hs" && \
    nix-build -v && \
    nix-store --gc && \
    rm -rf $HOME/.cache

WORKDIR /app/verismith/
ENTRYPOINT ["/app/verismith/result/bin/verismith"]