FROM node:22-slim

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* yarn.lock* ./

# Install dependencies
RUN npm install

# Copy source code
COPY . .

# Expose the development server port
EXPOSE 5173

# Run the development server
# VITE_* env vars from docker-compose are automatically available via import.meta.env
CMD npm run dev -- --host
