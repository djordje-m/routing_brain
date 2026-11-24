FROM node:22-slim

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* yarn.lock* ./

# Install dependencies
RUN npm install --frozen-lockfile 2>/dev/null || npm install

# Copy source code
COPY . .

# Expose the development server port
EXPOSE 5173

# Create .env file from environment variables and run dev server
CMD sh -c 'echo "VITE_API_BASE=${VITE_API_BASE}" > .env && echo "VITE_WS_URL=${VITE_WS_URL}" >> .env && npm run dev -- --host'
