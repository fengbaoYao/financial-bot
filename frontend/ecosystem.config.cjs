const NULL_DEVICE = process.platform === "win32" ? "NUL" : "/dev/null";

module.exports = {
  apps: [
    {
      name: "financial-bot-api",
      script: "./scripts/run_api_prod.sh",
      interpreter: "bash",
      out_file: NULL_DEVICE,
      error_file: NULL_DEVICE,
      merge_logs: true,
      env: {
        NODE_ENV: "production",
      },
    },
    {
      name: "financial-bot-web",
      script: "./scripts/run_web_prod.sh",
      interpreter: "bash",
      out_file: NULL_DEVICE,
      error_file: NULL_DEVICE,
      merge_logs: true,
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
