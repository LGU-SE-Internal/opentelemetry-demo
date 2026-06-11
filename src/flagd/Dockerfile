FROM ghcr.io/open-feature/flagd:v0.10.1

# Copy demo flags config
COPY demo.flagd.json /etc/flagd/demo_flags.json

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["start"]
