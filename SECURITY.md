# Security

Local ImageGen UI is a single-user desktop app. Its HTTP API binds to `127.0.0.1` and has no authentication. Keep it on loopback and use model files from trusted publishers. Store runtime files in dedicated directories without symlinks or junctions.

## Dependencies

- **Pillow 13.0.0** includes image-decoder fixes for issues affecting 12.3.0. [Pillow 13.0.0 security fixes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html#security)
- **PyTorch 2.6.0 / torchvision 0.21.0** is the CUDA profile used for the recorded BF16 runs. PyTorch 2.6.0 has known issues, including one in `torch.jit.script`. The review did not establish whether this issue is reachable through the app's image-generation workflow. [PyTorch advisory for CVE-2025-3000](https://github.com/advisories/GHSA-rrmf-rvhw-rf47)

The dependency review covered direct pins and verified the identities of the pinned Diffusers revision and stable-diffusion.cpp release. Transitive dependencies and downloaded native libraries were outside the review.

## Reporting

Use GitHub's private vulnerability reporting when available. Keep prompts, images, local paths and credentials out of public reports.
