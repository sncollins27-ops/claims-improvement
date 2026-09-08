const path = require("path");

const root = path.resolve(__dirname, "..");

module.exports = {
  apps: [
    {
      name: "claims-miner-sn111-improved",
      cwd: root,
      script: path.join(root, "scripts/run_mainnet_miner_pm2.sh"),
      interpreter: "/bin/bash",
      autorestart: true,
      watch: false,
      kill_timeout: 20000,
      min_uptime: "30s",
      max_restarts: 8,
      restart_delay: 20000,
      exp_backoff_restart_delay: 20000,
      time: true,
      out_file: path.join(root, "runs/neuron/mainnet/pm2-out.log"),
      error_file: path.join(root, "runs/neuron/mainnet/pm2-error.log"),
      merge_logs: true,
    },
  ],
};
