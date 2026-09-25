-- CreateTable
CREATE TABLE "app_session" (
    "id" TEXT NOT NULL,
    "user_id" INTEGER NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "last_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "user_agent" TEXT,

    CONSTRAINT "app_session_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "app_session_user_id_idx" ON "app_session"("user_id");

-- CreateIndex
CREATE INDEX "app_session_expires_at_idx" ON "app_session"("expires_at");

-- AddForeignKey
ALTER TABLE "app_session" ADD CONSTRAINT "app_session_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "app_user"("id") ON DELETE CASCADE ON UPDATE CASCADE;
